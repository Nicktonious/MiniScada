"""
Модуль эмулятора Modbus Slave устройства (EmuModbusSlaveDevice).

Обеспечивает:
1. Загрузку конфигурации (основная схема Modbus + вспомогательная схема симуляции).
2. Инициализацию словарей регистров Modbus (DI, CO, IR, HR).
3. Периодическое обновление данных по интервалу с поддержкой математических функций и генераторов.
4. Управление жизненным циклом: Run(), Stop(), start(), stop(), контекстный менеджер.
5. Интеграцию с pymodbus (при наличии) и подписку на обновления для моков/тестирования/БД.
"""

from __future__ import annotations

import json
import logging
import math
import pathlib
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Union

# Обеспечиваем доступ к модулям Common при прямом запуске
_CURRENT_DIR = pathlib.Path(__file__).resolve().parent
_PROJECT_ROOT = _CURRENT_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from Common.config_models import (
    ChannelType,
    ModbusDeviceConfig,
    RegisterTable,
    build_initial_memory_maps,
    decode_float32,
    encode_float32,
)

from ModbusSlaveEmu.device.generators import (
    BaseSignalGenerator,
    ConstantGenerator,
    CustomFunctionGenerator,
    GeneratorRegistry,
    LinearRampGenerator,
    RandomWalkGenerator,
    SineWaveGenerator,
    ToggleGenerator,
)
from ModbusSlaveEmu.device.schemas import AuxSimulationConfig, ChannelSimulationProfile

logger = logging.getLogger("ModbusSlaveEmu.Device")


class EmuModbusSlaveDevice:
    """
    Класс эмулятора ведомого устройства Modbus (Slave / Server).

    Хранит внутреннее состояние регистров Modbus и периодически обновляет их
    согласно математическим моделям каналов и калибровочным коэффициентам.
    """

    def __init__(
        self,
        config: Union[ModbusDeviceConfig, dict, str, pathlib.Path],
        aux_config: Optional[Union[AuxSimulationConfig, dict, str, pathlib.Path]] = None,
        update_interval_ms: Optional[int] = None,
    ) -> None:
        """
        :param config: Основная конфигурация устройства (объект, dict или путь к JSON).
        :param aux_config: Вспомогательная конфигурация симуляции (профили, мат. функции).
        :param update_interval_ms: Переопределение интервала обновления данных (мс).
        """
        # 1. Загрузка основной конфигурации
        self.config: ModbusDeviceConfig = self._load_device_config(config)

        # 2. Загрузка вспомогательной конфигурации симуляции
        self.aux_config: AuxSimulationConfig = self._load_aux_config(aux_config)

        # 3. Определение интервала обновления
        if update_interval_ms is not None:
            self.update_interval_ms = max(10, int(update_interval_ms))
        elif self.aux_config and self.aux_config.update_interval_ms:
            self.update_interval_ms = max(10, int(self.aux_config.update_interval_ms))
        elif self.config.polling and self.config.polling.poll_interval_ms:
            self.update_interval_ms = max(10, int(self.config.polling.poll_interval_ms))
        else:
            self.update_interval_ms = 500

        # 4. Инициализация словарей регистров Modbus (Thread-safe)
        self._lock = threading.RLock()
        self.data_bank: Dict[str, Dict[int, int]] = {
            "di": {},  # Discrete Inputs (address -> 0 or 1)
            "co": {},  # Coils (address -> 0 or 1)
            "ir": {},  # Input Registers (address -> 16-bit uint)
            "hr": {},  # Holding Registers (address -> 16-bit uint)
        }
        self._init_memory()

        # 5. Инициализация генераторов физических сигналов для каналов
        self._generators: Dict[str, BaseSignalGenerator] = {}
        self._init_generators()

        # 6. Управление фоновым потоком симуляции
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0: float = time.monotonic()
        self._last_tick_time: float = self._t0

        # 7. Подписчики на события обновления (для моков, логов, тестов, сохранения в БД)
        self._callbacks: List[Callable[[EmuModbusSlaveDevice, Dict[str, Any]], None]] = []

    # =========================================================================
    # Инициализация конфигурации и регистров
    # =========================================================================

    def _load_device_config(
        self, config: Union[ModbusDeviceConfig, dict, str, pathlib.Path]
    ) -> ModbusDeviceConfig:
        if isinstance(config, ModbusDeviceConfig):
            return config
        if isinstance(config, (str, pathlib.Path)):
            with open(config, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ModbusDeviceConfig.model_validate(data)
        if isinstance(config, dict):
            return ModbusDeviceConfig.model_validate(config)
        raise ValueError(f"Неподдерживаемый формат конфигурации устройства: {type(config)}")

    def _load_aux_config(
        self, aux_config: Optional[Union[AuxSimulationConfig, dict, str, pathlib.Path]]
    ) -> AuxSimulationConfig:
        if aux_config is None:
            return AuxSimulationConfig()
        if isinstance(aux_config, AuxSimulationConfig):
            return aux_config
        if isinstance(aux_config, (str, pathlib.Path)):
            with open(aux_config, "r", encoding="utf-8") as f:
                data = json.load(f)
            return AuxSimulationConfig.model_validate(data)
        if isinstance(aux_config, dict):
            return AuxSimulationConfig.model_validate(aux_config)
        return AuxSimulationConfig()

    def _init_memory(self) -> None:
        """Заполняет внутренние таблицы регистров начальными значениями по схеме."""
        with self._lock:
            initial_maps = build_initial_memory_maps(self.config)
            self.data_bank["di"] = initial_maps.get("di", {}).copy()
            self.data_bank["co"] = initial_maps.get("co", {}).copy()
            self.data_bank["ir"] = initial_maps.get("ir", {}).copy()
            self.data_bank["hr"] = initial_maps.get("hr", {}).copy()

    def _init_generators(self) -> None:
        """Создает генераторы сигналов для каждого канала."""
        for ch in self.config.channels:
            # Проверяем переопределение во вспомогательной конфигурации
            aux_profile = self.aux_config.channel_profiles.get(ch.channel_id)

            if aux_profile and aux_profile.custom_func:
                gen = CustomFunctionGenerator(
                    func=aux_profile.custom_func,
                    initial_value=aux_profile.initial_value or 0.0,
                    noise_amplitude=aux_profile.noise_amplitude or 0.0,
                    min_val=aux_profile.min_val or (ch.physical_range.min_val if ch.physical_range else None),
                    max_val=aux_profile.max_val or (ch.physical_range.max_val if ch.physical_range else None),
                )
                self._generators[ch.channel_id] = gen
                continue

            # Инициализация по типу канала
            if ch.type == ChannelType.DI:
                init_val = False
                toggle_sec = 5.0
                if ch.simulation:
                    init_val = bool(ch.simulation.initial_value or False)
                    toggle_sec = ch.simulation.toggle_interval_seconds or 5.0

                sim_pattern = None
                if ch.simulation and ch.simulation.pattern:
                    sim_pattern = getattr(ch.simulation.pattern, "value", str(ch.simulation.pattern))
                pattern = (aux_profile.generator_type if aux_profile else None) or sim_pattern or "toggle"
                if pattern == "constant":
                    self._generators[ch.channel_id] = ConstantGenerator(1.0 if init_val else 0.0)
                else:
                    self._generators[ch.channel_id] = ToggleGenerator(
                        initial_value=init_val,
                        toggle_interval_seconds=toggle_sec,
                        random_factor=0.2,
                    )

            elif ch.type == ChannelType.DO:
                init_val = 0.0
                if ch.simulation and ch.simulation.initial_value is not None:
                    init_val = 1.0 if ch.simulation.initial_value else 0.0
                self._generators[ch.channel_id] = ConstantGenerator(value=init_val)

            elif ch.type == ChannelType.AI:
                min_v = ch.physical_range.min_val if ch.physical_range else 0.0
                max_v = ch.physical_range.max_val if ch.physical_range else 100.0
                init_v = (min_v + max_v) / 2.0
                noise_amp = 0.0
                period_sec = 10.0
                pattern = "sine"

                if ch.simulation:
                    if ch.simulation.initial_value is not None:
                        init_v = float(ch.simulation.initial_value)
                    if ch.simulation.noise_amplitude is not None:
                        noise_amp = float(ch.simulation.noise_amplitude)
                    if ch.simulation.period_seconds is not None:
                        period_sec = float(ch.simulation.period_seconds)
                    if ch.simulation.pattern:
                        pattern = getattr(ch.simulation.pattern, "value", str(ch.simulation.pattern))

                if aux_profile:
                    if aux_profile.generator_type:
                        pattern = aux_profile.generator_type
                    if aux_profile.initial_value is not None:
                        init_v = float(aux_profile.initial_value)
                    if aux_profile.noise_amplitude is not None:
                        noise_amp = float(aux_profile.noise_amplitude)
                    if aux_profile.period_seconds is not None:
                        period_sec = float(aux_profile.period_seconds)

                if pattern == "sine":
                    amplitude = (max_v - min_v) * 0.35
                    self._generators[ch.channel_id] = SineWaveGenerator(
                        amplitude=amplitude,
                        period_seconds=period_sec,
                        offset=init_v,
                        noise_amplitude=noise_amp,
                        min_val=min_v,
                        max_val=max_v,
                    )
                elif pattern == "random_walk":
                    self._generators[ch.channel_id] = RandomWalkGenerator(
                        initial_value=init_v,
                        step_std=(max_v - min_v) * 0.02,
                        mean_target=init_v,
                        reversion_speed=0.05,
                        min_val=min_v,
                        max_val=max_v,
                        noise_amplitude=noise_amp,
                    )
                elif pattern == "linear_ramp":
                    self._generators[ch.channel_id] = LinearRampGenerator(
                        min_val=min_v,
                        max_val=max_v,
                        period_seconds=period_sec,
                        noise_amplitude=noise_amp,
                    )
                else:
                    self._generators[ch.channel_id] = ConstantGenerator(
                        value=init_v,
                        noise_amplitude=noise_amp,
                        min_val=min_v,
                        max_val=max_v,
                    )

    # =========================================================================
    # Подключение математических функций и генераторов
    # =========================================================================

    def set_math_function(
        self,
        channel_id: str,
        func: Callable[[float, float], float],
        noise_amplitude: float = 0.0,
        min_val: Optional[float] = None,
        max_val: Optional[float] = None,
    ) -> None:
        """
        Назначает пользовательскую математическую функцию для генерации сигнала канала.

        :param channel_id: Идентификатор канала.
        :param func: Функция вида f(t: float, dt: float) -> float.
        :param noise_amplitude: Амплитуда псевдослучайного шума.
        :param min_val: Нижняя граница отсечения.
        :param max_val: Верхняя граница отсечения.
        """
        ch = next((c for c in self.config.channels if c.channel_id == channel_id), None)
        if not ch:
            raise KeyError(f"Канал '{channel_id}' не найден в конфигурации устройства.")

        p_min = min_val if min_val is not None else (ch.physical_range.min_val if ch.physical_range else None)
        p_max = max_val if max_val is not None else (ch.physical_range.max_val if ch.physical_range else None)

        with self._lock:
            self._generators[channel_id] = CustomFunctionGenerator(
                func=func,
                noise_amplitude=noise_amplitude,
                min_val=p_min,
                max_val=p_max,
            )
            logger.info("Для канала '%s' установлена пользовательская мат. функция", channel_id)

    def set_generator(self, channel_id: str, generator: BaseSignalGenerator) -> None:
        """Устанавливает экземпляр генератора сигнала для указанного канала."""
        ch = next((c for c in self.config.channels if c.channel_id == channel_id), None)
        if not ch:
            raise KeyError(f"Канал '{channel_id}' не найден в конфигурации устройства.")
        with self._lock:
            self._generators[channel_id] = generator

    def add_subscriber(self, callback: Callable[[EmuModbusSlaveDevice, Dict[str, Any]], None]) -> None:
        """
        Добавляет функцию обратного вызова, вызываемую на каждом такте обновления.
        Удобно для моков, наполнения тестовой БД и телеметрии.
        """
        self._callbacks.append(callback)

    # =========================================================================
    # Логика обновления словаря регистров (Tick)
    # =========================================================================

    def tick(self, t: Optional[float] = None, dt: Optional[float] = None) -> Dict[str, Any]:
        """
        Выполняет один шаг обновления всех регистров устройства.
        Считывает актуальные k и b из Holding Registers, генерирует физическое
        значение по мат. функции и рассчитывает сырой код x для Modbus.
        """
        now = time.monotonic() if t is None else t
        if dt is None:
            dt = now - self._last_tick_time if now > self._last_tick_time else (self.update_interval_ms / 1000.0)
        self._last_tick_time = now

        snapshot: Dict[str, Any] = {
            "timestamp": time.time(),
            "device_id": self.config.device_id,
            "channels": {},
        }

        with self._lock:
            for ch in self.config.channels:
                gen = self._generators.get(ch.channel_id)
                addr = ch.modbus.address

                if ch.type == ChannelType.DI:
                    val_float = gen.generate(now, dt) if gen else 0.0
                    val_bool = bool(val_float >= 0.5)
                    int_val = 1 if val_bool else 0
                    if ch.modbus.table == RegisterTable.DISCRETE_INPUTS:
                        self.data_bank["di"][addr] = int_val
                    else:
                        self.data_bank["co"][addr] = int_val

                    snapshot["channels"][ch.channel_id] = {
                        "type": "DI",
                        "raw": int_val,
                        "eng": val_bool,
                        "table": getattr(ch.modbus.table, "value", str(ch.modbus.table)),
                        "address": addr,
                    }

                elif ch.type == ChannelType.DO:
                    # Для DO эмулятор сохраняет установленное значение (может меняться по Modbus Write)
                    current_val = self.data_bank["co"].get(addr, 0)
                    snapshot["channels"][ch.channel_id] = {
                        "type": "DO",
                        "raw": current_val,
                        "eng": bool(current_val),
                        "table": getattr(ch.modbus.table, "value", str(ch.modbus.table)),
                        "address": addr,
                    }

                elif ch.type == ChannelType.AI:
                    # 1. Генерируем физическую величину y
                    eng_y = gen.generate(now, dt) if gen else 0.0

                    # 2. Считываем актуальные k и b из Holding Registers
                    k_val = ch.coefficients.k.default_value if ch.coefficients else 1.0
                    b_val = ch.coefficients.b.default_value if ch.coefficients else 0.0

                    if ch.coefficients:
                        k_addr = ch.coefficients.k.address
                        b_addr = ch.coefficients.b.address
                        if k_addr in self.data_bank["hr"] and (k_addr + 1) in self.data_bank["hr"]:
                            k_regs = [self.data_bank["hr"][k_addr], self.data_bank["hr"][k_addr + 1]]
                            k_val = decode_float32(k_regs, ch.coefficients.k.byte_order, ch.coefficients.k.word_order)

                        if b_addr in self.data_bank["hr"] and (b_addr + 1) in self.data_bank["hr"]:
                            b_regs = [self.data_bank["hr"][b_addr], self.data_bank["hr"][b_addr + 1]]
                            b_val = decode_float32(b_regs, ch.coefficients.b.byte_order, ch.coefficients.b.word_order)

                    # 3. Вычисляем сырой код регистра: x = (y - b) / k
                    if math.isclose(k_val, 0.0):
                        raw_x = 0
                    else:
                        raw_x = int(round((eng_y - b_val) / k_val))

                    # 4. Ограничиваем диапазоном АЦП
                    raw_min = ch.physical_range.raw_min if ch.physical_range else 0
                    raw_max = ch.physical_range.raw_max if ch.physical_range else 65535
                    clamped_x = max(raw_min, min(raw_max, raw_x))

                    # 5. Записываем в таблицу регистров
                    target_table = "ir" if ch.modbus.table == RegisterTable.INPUT_REGISTERS else "hr"
                    self.data_bank[target_table][addr] = clamped_x

                    snapshot["channels"][ch.channel_id] = {
                        "type": "AI",
                        "raw": clamped_x,
                        "eng": round(eng_y, 4),
                        "unit": ch.physical_range.unit if ch.physical_range else "",
                        "k": k_val,
                        "b": b_val,
                        "table": target_table,
                        "address": addr,
                    }

        # Уведомляем подписчиков (моки, БД, логи)
        for cb in self._callbacks:
            try:
                cb(self, snapshot)
            except Exception as e:
                logger.error("Ошибка в callback подписчика: %s", e)

        return snapshot

    # =========================================================================
    # Управление жизненным циклом (Run, Stop, start, stop)
    # =========================================================================

    def _worker_loop(self) -> None:
        """Фоновый цикл генерации и обновления данных."""
        interval_s = self.update_interval_ms / 1000.0
        logger.info("Эмулятор устройства '%s' запущен (интервал: %d мс)", self.config.device_id, self.update_interval_ms)

        while not self._stop_event.is_set():
            t_start = time.monotonic()
            try:
                self.tick(t=t_start)
            except Exception as e:
                logger.error("Исключение в такте симуляции '%s': %s", self.config.device_id, e)

            elapsed = time.monotonic() - t_start
            sleep_time = max(0.005, interval_s - elapsed)
            if self._stop_event.wait(timeout=sleep_time):
                break

        logger.info("Эмулятор устройства '%s' остановлен", self.config.device_id)

    def run(self, blocking: bool = False) -> None:
        """
        Запускает периодическое обновление регистров устройства.

        :param blocking: Если True, блокирует текущий поток до вызова stop() / Ctrl+C.
                         Если False (по умолчанию), запускает фоновый поток-демон.
        """
        with self._lock:
            if self._running:
                logger.warning("Устройство '%s' уже запущено", self.config.device_id)
                return
            self._running = True
            self._stop_event.clear()
            self._t0 = time.monotonic()
            self._last_tick_time = self._t0

            if blocking:
                self._worker_loop()
            else:
                self._thread = threading.Thread(
                    target=self._worker_loop,
                    name=f"EmuModbusSlave-{self.config.device_id}",
                    daemon=True,
                )
                self._thread.start()

    def Run(self, blocking: bool = False) -> None:
        """Алиас для метода run() с заглавной буквы."""
        self.run(blocking=blocking)

    def start(self) -> None:
        """Запускает эмулятор в фоновом режиме."""
        self.run(blocking=False)

    def Start(self) -> None:
        """Алиас для метода start()."""
        self.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Останавливает периодическое обновление данных."""
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None

    def Stop(self, timeout: float = 2.0) -> None:
        """Алиас для метода stop() с заглавной буквы."""
        self.stop(timeout=timeout)

    @property
    def is_running(self) -> bool:
        """Возвращает текущий статус работы эмулятора."""
        return self._running

    # =========================================================================
    # Доступ к словарям регистров
    # =========================================================================

    def get_register(self, table: str, address: int) -> int:
        """Возвращает значение указанного регистра."""
        with self._lock:
            tbl = self.data_bank.get(table.lower())
            if tbl is None:
                raise KeyError(f"Таблица регистров '{table}' не существует")
            return tbl.get(address, 0)

    def set_register(self, table: str, address: int, value: int) -> None:
        """Устанавливает значение регистра (эмулирует Modbus Write)."""
        with self._lock:
            tbl = self.data_bank.get(table.lower())
            if tbl is None:
                raise KeyError(f"Таблица регистров '{table}' не существует")
            tbl[address] = int(value)

    def get_data_bank_snapshot(self) -> Dict[str, Dict[int, int]]:
        """Возвращает полную копию словарей всех таблиц регистров."""
        with self._lock:
            return {
                "di": self.data_bank["di"].copy(),
                "co": self.data_bank["co"].copy(),
                "ir": self.data_bank["ir"].copy(),
                "hr": self.data_bank["hr"].copy(),
            }

    # =========================================================================
    # Поддержка контекстного менеджера (with)
    # =========================================================================

    def __enter__(self) -> EmuModbusSlaveDevice:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

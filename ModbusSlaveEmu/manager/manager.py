"""
Модуль ModbusSlaveManager для управления группой эмулируемых Modbus Slave устройств.

Обеспечивает:
1. Инициализацию N устройств из папки конфигураций по схеме config.schema.json.
2. Мониторинг (observe) обновлений от всех ведомых устройств.
3. Унифицированный интерфейс чтения и записи по modbusId (Slave ID),
   таблице/регистру, переданным значениям и количеству байт.
4. Прямую совместимость с протоколом pymodbus (getValues / setValues).
5. Централизованное управление жизненным циклом (Run / Stop).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import struct
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# Обеспечиваем пути импорта проекта
_CURRENT_DIR = pathlib.Path(__file__).resolve().parent
_PROJECT_ROOT = _CURRENT_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from Common.config_models import ModbusDeviceConfig
from ModbusSlaveEmu.device import AuxSimulationConfig, EmuModbusSlaveDevice

logger = logging.getLogger("ModbusSlaveEmu.Manager")

ObserverCallback = Callable[[int, str, Dict[str, Any]], None]


class ModbusSlaveManager:
    """
    Менеджер пула эмулируемых Modbus Slave устройств.

    Загружает конфигурации из каталога, регистрирует обработчики событий обновления
    и предоставляет API для чтения/записи регистров и сырых байтов.
    """

    # Соответствие Modbus Function Code -> внутреннее имя таблицы
    FC_TO_TABLE: Dict[int, str] = {
        1: "co",   # Read Coils
        2: "di",   # Read Discrete Inputs
        3: "hr",   # Read Holding Registers
        4: "ir",   # Read Input Registers
        5: "co",   # Write Single Coil
        6: "hr",   # Write Single Register
        15: "co",  # Write Multiple Coils
        16: "hr",  # Write Multiple Registers
    }

    TABLE_ALIASES: Dict[str, str] = {
        "coils": "co",
        "coil": "co",
        "co": "co",
        "0x": "co",
        "discrete_inputs": "di",
        "discrete_input": "di",
        "di": "di",
        "1x": "di",
        "input_registers": "ir",
        "input_register": "ir",
        "ir": "ir",
        "3x": "ir",
        "holding_registers": "hr",
        "holding_register": "hr",
        "hr": "hr",
        "4x": "hr",
    }

    def __init__(
        self,
        config_dir: Union[str, pathlib.Path],
        aux_config_dir: Optional[Union[str, pathlib.Path]] = None,
        update_interval_ms: Optional[int] = None,
        auto_start: bool = False,
    ) -> None:
        """
        :param config_dir: Каталог с JSON-конфигурациями устройств (по схеме config.schema.json).
        :param aux_config_dir: Опциональный каталог со вспомогательными схемами симуляции.
        :param update_interval_ms: Общий интервал обновления (переопределяет параметры в файлах).
        :param auto_start: Если True, сразу запускает симуляцию всех устройств.
        """
        self.config_dir = pathlib.Path(config_dir).resolve()
        self.aux_config_dir = pathlib.Path(aux_config_dir).resolve() if aux_config_dir else None
        self.update_interval_ms = update_interval_ms

        self._lock = threading.RLock()
        self._devices_by_slave_id: Dict[int, EmuModbusSlaveDevice] = {}
        self._devices_by_id: Dict[str, EmuModbusSlaveDevice] = {}
        self._latest_snapshots: Dict[int, Dict[str, Any]] = {}
        self._observers: List[ObserverCallback] = []
        self._is_running = False

        # Загрузка устройств из каталога
        self._load_devices()

        if auto_start:
            self.start()

    # =========================================================================
    # 1. Загрузка и инициализация N slave-устройств
    # =========================================================================

    def _load_devices(self) -> None:
        """Сканирует каталог и инициализирует slave-устройства."""
        if not self.config_dir.exists() or not self.config_dir.is_dir():
            raise FileNotFoundError(f"Каталог конфигураций не найден: {self.config_dir}")

        json_files = sorted(self.config_dir.glob("*.json"))
        loaded_count = 0

        for file_path in json_files:
            # Пропускаем файлы схем, если они лежат в этой же папке
            if file_path.name.endswith(".schema.json"):
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)

                # Проверяем, что это конфигурация Modbus-устройства
                if not isinstance(raw_data, dict) or "slave_id" not in raw_data or "channels" not in raw_data:
                    logger.debug("Файл %s пропущен (не содержит полей slave_id/channels)", file_path.name)
                    continue

                device_config = ModbusDeviceConfig.model_validate(raw_data)
                slave_id = device_config.slave_id
                device_id = device_config.device_id

                if slave_id in self._devices_by_slave_id:
                    existing = self._devices_by_slave_id[slave_id].config.device_id
                    raise ValueError(
                        f"Конфликт Modbus Slave ID {slave_id}: файл '{file_path.name}' "
                        f"дублирует адрес уже загруженного устройства '{existing}'"
                    )

                # Проверяем наличие вспомогательной схемы симуляции для данного устройства
                aux_config = None
                if self.aux_config_dir and self.aux_config_dir.exists():
                    aux_file = self.aux_config_dir / f"{device_id}.sim.json"
                    if not aux_file.exists():
                        aux_file = self.aux_config_dir / f"{file_path.stem}.sim.json"
                    if aux_file.exists():
                        with open(aux_file, "r", encoding="utf-8") as af:
                            aux_config = AuxSimulationConfig.model_validate(json.load(af))

                # Инициализация устройства
                device = EmuModbusSlaveDevice(
                    config=device_config,
                    aux_config=aux_config,
                    update_interval_ms=self.update_interval_ms,
                )

                # Подписка менеджера на обновления устройства (Observer pattern)
                device.add_subscriber(self._handle_device_tick)

                self._devices_by_slave_id[slave_id] = device
                self._devices_by_id[device_id] = device
                loaded_count += 1
                logger.info(
                    "Загружено устройство '%s' (Slave ID: %d, Каналов: %d) из %s",
                    device_id,
                    slave_id,
                    len(device_config.channels),
                    file_path.name,
                )

            except Exception as e:
                logger.error("Ошибка загрузки конфигурации из '%s': %s", file_path.name, e)
                raise

        logger.info(
            "Инициализация завершена. Всего загружено slave-устройств: %d", loaded_count
        )

    # =========================================================================
    # 2. Мониторинг (Observer) обновлений устройств
    # =========================================================================

    def _handle_device_tick(self, device: EmuModbusSlaveDevice, snapshot: Dict[str, Any]) -> None:
        """Внутренний обработчик такта обновления slave-устройства."""
        slave_id = device.config.slave_id
        device_id = device.config.device_id

        with self._lock:
            self._latest_snapshots[slave_id] = snapshot

        # Уведомляем внешних наблюдателей
        for observer in list(self._observers):
            try:
                observer(slave_id, device_id, snapshot)
            except Exception as e:
                logger.error("Ошибка в наблюдателе (observer) для Slave ID %d: %s", slave_id, e)

    def add_observer(self, observer: ObserverCallback) -> None:
        """
        Регистрирует внешнего наблюдателя за обновлениями устройств.
        Сигнатура наблюдателя: callback(slave_id: int, device_id: str, snapshot: dict) -> None
        """
        with self._lock:
            if observer not in self._observers:
                self._observers.append(observer)

    def remove_observer(self, observer: ObserverCallback) -> None:
        """Удаляет зарегистрированного наблюдателя."""
        with self._lock:
            if observer in self._observers:
                self._observers.remove(observer)

    def get_snapshot(self, slave_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает последний снимок состояния устройства по его slave_id."""
        with self._lock:
            snap = self._latest_snapshots.get(slave_id)
            return snap.copy() if snap else None

    def get_all_snapshots(self) -> Dict[int, Dict[str, Any]]:
        """Возвращает последние снимки состояния всех устройств."""
        with self._lock:
            return {sid: s.copy() for sid, s in self._latest_snapshots.items()}

    # =========================================================================
    # 3. Унифицированный интерфейс чтения и записи
    # =========================================================================

    def _normalize_table(self, table: Union[str, int]) -> str:
        """Приводит строковое имя таблицы или Function Code к коду 'di', 'co', 'ir', 'hr'."""
        if isinstance(table, int):
            norm = self.FC_TO_TABLE.get(table)
            if not norm:
                raise ValueError(f"Неподдерживаемый Modbus Function Code: {table}")
            return norm

        key = str(table).lower().strip()
        norm = self.TABLE_ALIASES.get(key)
        if not norm:
            raise ValueError(f"Неизвестная таблица Modbus: '{table}'. Доступны: coils, discrete_inputs, input_registers, holding_registers")
        return norm

    def _get_device_or_raise(self, slave_id: int) -> EmuModbusSlaveDevice:
        """Возвращает устройство по slave_id или выбрасывает KeyError."""
        device = self._devices_by_slave_id.get(slave_id)
        if not device:
            raise KeyError(
                f"Устройство с Modbus Slave ID {slave_id} не найдено в менеджере. "
                f"Доступные ID: {list(self._devices_by_slave_id.keys())}"
            )
        return device

    def read(
        self,
        slave_id: int,
        table: Union[str, int],
        address: int,
        quantity: int = 1,
        as_bytes: bool = False,
    ) -> Union[List[int], List[bool], bytes]:
        """
        Чтение регистров или дискретных бит из устройства.

        :param slave_id: Modbus Unit ID / Slave ID.
        :param table: Имя таблицы ('coils', 'discrete_inputs', 'input_registers', 'holding_registers')
                      или Modbus Function Code (1, 2, 3, 4).
        :param address: 0-based начальный адрес регистра/бита.
        :param quantity: Количество считываемых регистров или бит.
        :param as_bytes: Если True, возвращает сериализованную байтовую строку.
        :return: Список значений (int/bool) или bytes (если as_bytes=True).
        """
        device = self._get_device_or_raise(slave_id)
        norm_table = self._normalize_table(table)

        with device._lock:
            tbl_dict = device.data_bank.get(norm_table, {})
            is_bit_table = norm_table in ("co", "di")

            if is_bit_table:
                bits: List[bool] = [bool(tbl_dict.get(address + i, 0)) for i in range(quantity)]
                if not as_bytes:
                    return bits
                # Упаковка бит в байты по стандарту Modbus (LSB-first, 8 бит на байт)
                byte_arr = bytearray((quantity + 7) // 8)
                for idx, bit in enumerate(bits):
                    if bit:
                        byte_arr[idx // 8] |= (1 << (idx % 8))
                return bytes(byte_arr)
            else:
                words: List[int] = [tbl_dict.get(address + i, 0) for i in range(quantity)]
                if not as_bytes:
                    return words
                # Упаковка 16-битных слов в байты (Big-Endian per Modbus spec)
                byte_arr = bytearray()
                for word in words:
                    byte_arr.extend(struct.pack(">H", word & 0xFFFF))
                return bytes(byte_arr)

    def read_bytes(
        self,
        slave_id: int,
        table: Union[str, int],
        address: int,
        quantity_bytes: int,
    ) -> bytes:
        """
        Чтение данных из устройства с явным указанием количества байт (quantity_bytes).

        Для 16-битных регистров (IR, HR) quantity_bytes преобразуется в количество слов:
        words_count = (quantity_bytes + 1) // 2.
        Возвращает ровно quantity_bytes байт.
        """
        norm_table = self._normalize_table(table)
        if norm_table in ("co", "di"):
            # Для бит 1 байт = 8 бит
            quantity_bits = quantity_bytes * 8
            raw_bytes = self.read(slave_id, norm_table, address, quantity=quantity_bits, as_bytes=True)
            return raw_bytes[:quantity_bytes]
        else:
            # Для 16-битных регистров 2 байта = 1 регистр
            reg_count = (quantity_bytes + 1) // 2
            raw_bytes = self.read(slave_id, norm_table, address, quantity=reg_count, as_bytes=True)
            return raw_bytes[:quantity_bytes]

    def write(
        self,
        slave_id: int,
        table: Union[str, int],
        address: int,
        values: Union[int, bool, List[int], List[bool], bytes],
        quantity_bytes: Optional[int] = None,
    ) -> int:
        """
        Запись данных в регистры устройства (эмуляция Modbus Write).

        Поддерживает:
        - Одиночные значения (int или bool).
        - Списки значений (List[int] или List[bool]).
        - Сырые байты (bytes).

        :param slave_id: Modbus Unit ID / Slave ID.
        :param table: Имя таблицы ('coils', 'holding_registers') или FC (5, 6, 15, 16).
        :param address: 0-based начальный адрес.
        :param values: Записываемые данные (значение, список или байты).
        :param quantity_bytes: Опциональное ограничение по байтам при передаче bytes.
        :return: Количество успешно записанных элементов (регистров или бит).
        """
        device = self._get_device_or_raise(slave_id)
        norm_table = self._normalize_table(table)

        # Проверка допустимости записи в таблицу
        if norm_table not in ("co", "hr"):
            logger.warning(
                "Попытка записи в таблицу '%s', которая по стандарту Modbus доступна только для чтения (ReadOnly).",
                norm_table,
            )

        with device._lock:
            tbl_dict = device.data_bank.setdefault(norm_table, {})

            # 1. Запись сырых байтов
            if isinstance(values, (bytes, bytearray)):
                raw_data = bytes(values)
                if quantity_bytes is not None:
                    raw_data = raw_data[:quantity_bytes]

                if norm_table in ("co", "di"):
                    # Распаковываем биты из байтов
                    written_count = 0
                    for byte_idx, b in enumerate(raw_data):
                        for bit_idx in range(8):
                            target_addr = address + byte_idx * 8 + bit_idx
                            bit_val = 1 if ((b >> bit_idx) & 1) else 0
                            tbl_dict[target_addr] = bit_val
                            written_count += 1
                    return written_count
                else:
                    # Распаковываем 16-битные слова Big-Endian
                    words: List[int] = []
                    for i in range(0, len(raw_data) - (len(raw_data) % 2), 2):
                        w = struct.unpack(">H", raw_data[i : i + 2])[0]
                        words.append(w)
                    if len(raw_data) % 2 == 1:
                        # Если нечетное число байт, дополняем младшим нулем
                        words.append((raw_data[-1] << 8) & 0xFF00)

                    for offset, w in enumerate(words):
                        tbl_dict[address + offset] = w
                    return len(words)

            # 2. Запись списка значений
            elif isinstance(values, list):
                if norm_table in ("co", "di"):
                    for offset, v in enumerate(values):
                        tbl_dict[address + offset] = 1 if bool(v) else 0
                else:
                    for offset, v in enumerate(values):
                        tbl_dict[address + offset] = int(v) & 0xFFFF
                return len(values)

            # 3. Запись одиночного значения
            else:
                if norm_table in ("co", "di"):
                    tbl_dict[address] = 1 if bool(values) else 0
                else:
                    tbl_dict[address] = int(values) & 0xFFFF
                return 1

    def write_bytes(
        self,
        slave_id: int,
        table: Union[str, int],
        address: int,
        data: bytes,
    ) -> int:
        """Удобная обертка для записи сырых байтов."""
        return self.write(slave_id, table, address, values=data)

    # =========================================================================
    # 4. Методы прямого взаимодействия с адаптером pymodbus
    # =========================================================================

    def get_values(self, slave_id: int, fc: int, address: int, count: int = 1) -> List[Union[int, bool]]:
        """
        Сигнатура, совместимая с pymodbus ModbusSlaveContext.getValues(fc, address, count).
        Возвращает список значений бит или регистров.
        """
        result = self.read(slave_id=slave_id, table=fc, address=address, quantity=count, as_bytes=False)
        return list(result) if isinstance(result, list) else []

    def set_values(self, slave_id: int, fc: int, address: int, values: List[Union[int, bool]]) -> None:
        """
        Сигнатура, совместимая с pymodbus ModbusSlaveContext.setValues(fc, address, values).
        Записывает список значений в устройство.
        """
        self.write(slave_id=slave_id, table=fc, address=address, values=values)

    # =========================================================================
    # 5. Управление жизненным циклом (Run / Stop / start / stop)
    # =========================================================================

    def run(self, blocking: bool = False) -> None:
        """Запускает фоновое периодическое обновление всех slave-устройств."""
        with self._lock:
            if self._is_running:
                logger.warning("ModbusSlaveManager уже запущен")
                return
            self._is_running = True
            for dev in self._devices_by_slave_id.values():
                dev.run(blocking=False)
            logger.info("Запущены все slave-устройства (всего: %d)", len(self._devices_by_slave_id))

        if blocking:
            try:
                # Ожидание в основном потоке до прерывания
                stop_event = threading.Event()
                stop_event.wait()
            except KeyboardInterrupt:
                self.stop()

    def Run(self, blocking: bool = False) -> None:
        """Алиас для run()."""
        self.run(blocking=blocking)

    def start(self) -> None:
        """Запускает симуляцию всех устройств в фоне."""
        self.run(blocking=False)

    def Start(self) -> None:
        """Алиас для start()."""
        self.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Останавливает симуляцию всех slave-устройств."""
        with self._lock:
            if not self._is_running:
                return
            for dev in self._devices_by_slave_id.values():
                dev.stop(timeout=timeout)
            self._is_running = False
            logger.info("Все slave-устройства остановлены")

    def Stop(self, timeout: float = 2.0) -> None:
        """Алиас для stop()."""
        self.stop(timeout=timeout)

    @property
    def is_running(self) -> bool:
        """Флаг активности менеджера."""
        return self._is_running

    # =========================================================================
    # 6. Инспекция и доступ к устройствам
    # =========================================================================

    def get_device(self, slave_id: int) -> Optional[EmuModbusSlaveDevice]:
        """Возвращает экземпляр EmuModbusSlaveDevice по его slave_id."""
        return self._devices_by_slave_id.get(slave_id)

    def get_device_by_id(self, device_id: str) -> Optional[EmuModbusSlaveDevice]:
        """Возвращает экземпляр EmuModbusSlaveDevice по строковому device_id."""
        return self._devices_by_id.get(device_id)

    def get_all_devices(self) -> List[EmuModbusSlaveDevice]:
        """Возвращает список всех управляемых устройств."""
        return list(self._devices_by_slave_id.values())

    def list_slave_ids(self) -> List[int]:
        """Возвращает список всех зарегистрированных Modbus Slave ID."""
        return sorted(self._devices_by_slave_id.keys())

    def bridge(
        self,
        auto_start: bool = True,
        on_request: Optional[Callable[[str, int, int, int, Any], None]] = None,
    ) -> Any:
        """
        Создает и возвращает высокоуровневый шлюз ModbusBridge, который:
        1. Получает все шины (Modbus TCP, RTU) из конфигураций устройств.
        2. Запускает слушатели портов через функционал pymodbus.
        3. Читает данные, передает команды и формирует ответы в pymodbus.

        :param auto_start: Если True, сразу запускает слушатели всех шин.
        :param on_request: Опциональный callback для трассировки PDU запросов.
        :return: Экземпляр ModbusBridge.
        """
        from ModbusSlaveEmu.bridge.bridge import ModbusBridge
        return ModbusBridge(manager=self, auto_start=auto_start, on_request=on_request)

    def __enter__(self) -> ModbusSlaveManager:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

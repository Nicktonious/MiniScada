"""
Модуль строго типизированных моделей конфигурации Modbus-устройств.
Совместим с Pydantic v2 (с поддержкой загрузки из JSON и валидации).
Включает функции преобразования физических величин (y = k * x + b),
упаковки/распаковки регистров Modbus и генерации структур памяти для pymodbus.
"""

from __future__ import annotations

import math
import struct
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

try:
    from pydantic import BaseModel, Field, field_validator, model_validator
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    class _FieldInfo:
        def __init__(self, default=None, default_factory=None):
            self.default = default
            self.default_factory = default_factory

    def Field(*args, **kwargs):
        default = kwargs.get("default", None)
        default_factory = kwargs.get("default_factory", None)
        return _FieldInfo(default=default, default_factory=default_factory)

    class BaseModel:
        def __init__(self, **kwargs):
            for attr_name, attr_val in self.__class__.__dict__.items():
                if isinstance(attr_val, _FieldInfo):
                    if attr_val.default_factory is not None:
                        setattr(self, attr_name, attr_val.default_factory())
                    else:
                        setattr(self, attr_name, attr_val.default)
            for k, v in kwargs.items():
                setattr(self, k, v)

        @classmethod
        def _resolve_type(cls, type_hint: Any) -> Any:
            if isinstance(type_hint, str):
                clean_name = type_hint.replace("Optional[", "").replace("List[", "").replace("]", "").strip()
                return globals().get(clean_name)
            return type_hint

        @classmethod
        def model_validate(cls, data: Any):
            if isinstance(data, cls):
                return data
            if not isinstance(data, dict):
                return data

            annotations = getattr(cls, "__annotations__", {})
            kwargs = {}
            for k, v in data.items():
                raw_type = annotations.get(k)
                resolved = cls._resolve_type(raw_type)

                if isinstance(v, dict):
                    if isinstance(resolved, type) and issubclass(resolved, BaseModel):
                        kwargs[k] = resolved.model_validate(v)
                    else:
                        kwargs[k] = v
                elif isinstance(v, list):
                    if isinstance(resolved, type) and issubclass(resolved, BaseModel):
                        kwargs[k] = [resolved.model_validate(item) for item in v]
                    elif k == "channels":
                        channel_cls = globals().get("ChannelConfig")
                        if channel_cls:
                            kwargs[k] = [channel_cls.model_validate(item) for item in v]
                        else:
                            kwargs[k] = v
                    else:
                        kwargs[k] = v
                else:
                    kwargs[k] = v
            return cls(**kwargs)

        def model_dump(self) -> dict:
            return self.__dict__


# ============================================================================
# Перечисления (Enums)
# ============================================================================

class ConnectionType(str, Enum):
    TCP = "tcp"
    RTU = "rtu"


class RegisterTable(str, Enum):
    DISCRETE_INPUTS = "discrete_inputs"  # 1x (FC 02)
    COILS = "coils"                      # 0x (FC 01, 05, 15)
    INPUT_REGISTERS = "input_registers"  # 3x (FC 04)
    HOLDING_REGISTERS = "holding_registers"  # 4x (FC 03, 06, 16)


class DataType(str, Enum):
    BOOL = "bool"
    UINT16 = "uint16"
    INT16 = "int16"
    UINT32 = "uint32"
    INT32 = "int32"
    FLOAT32 = "float32"


class Endianness(str, Enum):
    BIG = "big"
    LITTLE = "little"


class ChannelType(str, Enum):
    DI = "DI"  # Digital Input
    DO = "DO"  # Digital Output
    AI = "AI"  # Analog Input


class SimulationPattern(str, Enum):
    CONSTANT = "constant"
    SINE = "sine"
    RANDOM_WALK = "random_walk"
    LINEAR_RAMP = "linear_ramp"
    TOGGLE = "toggle"
    PULSE = "pulse"


# ============================================================================
# Модели конфигурации устройства и связи
# ============================================================================

class ModbusTcpSettings(BaseModel):
    host: str = Field(..., description="IP-адрес или hostname Modbus TCP сервера")
    port: int = Field(default=502, ge=1, le=65535, description="Порт Modbus TCP")
    timeout_ms: int = Field(default=2000, ge=50, description="Таймаут запроса в мс")


class ModbusRtuSettings(BaseModel):
    port: str = Field(..., description="Последовательный порт, например /dev/ttyUSB0 или COM3")
    baudrate: int = Field(default=9600, description="Скорость бод (9600, 19200, 115200...)")
    data_bits: Literal[7, 8] = Field(default=8, description="Биты данных (7 или 8)")
    parity: Literal["N", "E", "O"] = Field(default="N", description="Четность (N, E, O)")
    stop_bits: Literal[1, 2] = Field(default=1, description="Стоповые биты (1 или 2)")
    timeout_ms: int = Field(default=1000, ge=50, description="Таймаут запроса в мс")


class ConnectionConfig(BaseModel):
    type: ConnectionType = Field(..., description="Тип протокола (tcp или rtu)")
    tcp: Optional[ModbusTcpSettings] = None
    rtu: Optional[ModbusRtuSettings] = None


class PollingConfig(BaseModel):
    enabled: bool = Field(default=True, description="Флаг активности опроса устройства")
    poll_interval_ms: int = Field(default=1000, ge=50, description="Периодичность опроса текущих данных")
    coefficients_poll_interval_ms: int = Field(
        default=60000,
        ge=1000,
        description="Период обновления коэффициентов k и b из Holding Registers"
    )
    max_retries: int = Field(default=3, ge=0, description="Количество повторных попыток при сбое связи")


# ============================================================================
# Модели Modbus-маппинга и каналов
# ============================================================================

class ModbusMapping(BaseModel):
    table: RegisterTable = Field(..., description="Таблица регистров Modbus")
    address: int = Field(..., ge=0, le=65535, description="0-based адрес смещения регистра")
    data_type: DataType = Field(default=DataType.UINT16, description="Тип данных регистра")
    byte_order: Endianness = Field(default=Endianness.BIG, description="Порядок байт (big/little)")
    word_order: Endianness = Field(default=Endianness.BIG, description="Порядок 16-битных слов для 32-бит типов")


class PhysicalRange(BaseModel):
    min_val: float = Field(..., description="Минимальное значение физической величины")
    max_val: float = Field(..., description="Максимальное значение физической величины")
    unit: str = Field(..., description="Единица измерения (bar, °C, mA, V...)")
    raw_min: int = Field(default=0, description="Минимальный код АЦП/сырого регистра")
    raw_max: int = Field(default=65535, description="Максимальный код АЦП/сырого регистра")


class CoefficientRegister(BaseModel):
    table: RegisterTable = Field(
        default=RegisterTable.HOLDING_REGISTERS,
        description="Коэффициенты обязаны храниться в Holding Registers"
    )
    address: int = Field(..., ge=0, le=65535, description="0-based адрес начального Holding Register")
    data_type: DataType = Field(default=DataType.FLOAT32, description="Тип данных коэффициента (обычно float32)")
    byte_order: Endianness = Field(default=Endianness.BIG, description="Порядок байт")
    word_order: Endianness = Field(default=Endianness.BIG, description="Порядок слов")
    default_value: float = Field(..., description="Значение по умолчанию")
    description: Optional[str] = Field(default=None, description="Описание назначения коэффициента")


class AICoefficients(BaseModel):
    k: CoefficientRegister = Field(..., description="Коэффициент наклона / масштабирования (Slope / Gain)")
    b: CoefficientRegister = Field(..., description="Смещение нуля / аддитивная добавка (Zero Offset / Bias)")


class SimulationConfig(BaseModel):
    pattern: SimulationPattern = Field(default=SimulationPattern.CONSTANT, description="Шаблон генерации")
    period_seconds: Optional[float] = Field(default=10.0, description="Период синусоиды или пилы в секундах")
    noise_amplitude: Optional[float] = Field(default=0.0, description="Амплитуда случайного шума")
    initial_value: Optional[Union[float, bool]] = Field(default=None, description="Начальное значение")
    toggle_interval_seconds: Optional[float] = Field(default=5.0, description="Интервал переключения для DI toggle")


class ChannelConfig(BaseModel):
    channel_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$", description="Уникальный ID канала")
    name: str = Field(..., description="Понятное название канала")
    description: Optional[str] = Field(default=None, description="Подробное описание канала")
    type: ChannelType = Field(..., description="Тип канала: DI, DO или AI")
    modbus: ModbusMapping = Field(..., description="Параметры Modbus адресации")
    physical_range: Optional[PhysicalRange] = Field(default=None, description="Физический диапазон (для AI)")
    coefficients: Optional[AICoefficients] = Field(default=None, description="Коэффициенты k и b (для AI)")
    simulation: Optional[SimulationConfig] = Field(default=None, description="Параметры симуляции")

    def to_engineering(self, raw_value: float, k: float, b: float) -> float:
        """
        Преобразует сырое значение АЦП/регистра в инженерную физическую величину:
        y = k * x + b
        """
        return (k * raw_value) + b

    def to_raw(self, engineering_value: float, k: float, b: float) -> int:
        """
        Преобразует инженерную величину обратно в сырой код регистра:
        x = (y - b) / k
        """
        if math.isclose(k, 0.0):
            return 0
        raw = (engineering_value - b) / k
        return int(round(raw))


class ModbusDeviceConfig(BaseModel):
    schema_version: Literal["1.0"] = Field(default="1.0", description="Версия формата конфигурации")
    device_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$", description="Уникальный ID устройства")
    name: str = Field(..., description="Название устройства")
    description: Optional[str] = Field(default=None, description="Описание устройства")
    slave_id: int = Field(..., ge=1, le=247, description="Modbus Unit ID / Slave ID")
    connection: ConnectionConfig = Field(..., description="Настройки связи")
    polling: PollingConfig = Field(default_factory=PollingConfig, description="Настройки опроса")
    channels: List[ChannelConfig] = Field(..., min_length=1, description="Список каналов устройства")


# ============================================================================
# Вспомогательные функции для кодирования/декодирования регистров Modbus
# ============================================================================

def encode_float32(
    value: float,
    byte_order: Union[Endianness, str] = Endianness.BIG,
    word_order: Union[Endianness, str] = Endianness.BIG
) -> List[int]:
    """
    Упаковывает 32-битное число с плавающей точкой IEEE 754 в два 16-битных регистра Modbus.
    """
    byte_order_str = byte_order.value if isinstance(byte_order, Endianness) else byte_order
    word_order_str = word_order.value if isinstance(word_order, Endianness) else word_order

    # Упаковываем float в 4 байта (big endian по стандарту IEEE 754)
    raw_bytes = struct.pack(">f", float(value))

    # Раскладываем на два 16-битных слова
    high_word = (raw_bytes[0] << 8) | raw_bytes[1]
    low_word = (raw_bytes[2] << 8) | raw_bytes[3]

    if byte_order_str == "little":
        high_word = ((high_word & 0xFF) << 8) | ((high_word >> 8) & 0xFF)
        low_word = ((low_word & 0xFF) << 8) | ((low_word >> 8) & 0xFF)

    if word_order_str == "little":
        return [low_word, high_word]
    return [high_word, low_word]


def decode_float32(
    registers: List[int],
    byte_order: Union[Endianness, str] = Endianness.BIG,
    word_order: Union[Endianness, str] = Endianness.BIG
) -> float:
    """
    Распаковывает два 16-битных регистра Modbus в 32-битное число float IEEE 754.
    """
    if len(registers) < 2:
        raise ValueError(f"Для декодирования float32 требуется 2 регистра, получено: {len(registers)}")

    byte_order_str = byte_order.value if isinstance(byte_order, Endianness) else byte_order
    word_order_str = word_order.value if isinstance(word_order, Endianness) else word_order

    w1, w2 = registers[0], registers[1]
    if word_order_str == "little":
        high_word, low_word = w2, w1
    else:
        high_word, low_word = w1, w2

    if byte_order_str == "little":
        high_word = ((high_word & 0xFF) << 8) | ((high_word >> 8) & 0xFF)
        low_word = ((low_word & 0xFF) << 8) | ((low_word >> 8) & 0xFF)

    b0 = (high_word >> 8) & 0xFF
    b1 = high_word & 0xFF
    b2 = (low_word >> 8) & 0xFF
    b3 = low_word & 0xFF

    raw_bytes = bytes([b0, b1, b2, b3])
    return struct.unpack(">f", raw_bytes)[0]


# ============================================================================
# Утилита инициализации адресного пространства для pymodbus
# ============================================================================

def build_initial_memory_maps(device_config: ModbusDeviceConfig) -> Dict[str, Dict[int, int]]:
    """
    Формирует начальные карты памяти Modbus-регистров для инициализации
    хранилища pymodbus (ModbusSparseDataBlock).

    Возвращает словарь:
    {
        "di": {address: bool/int},
        "co": {address: bool/int},
        "ir": {address: int},
        "hr": {address: int}
    }
    """
    memory: Dict[str, Dict[int, int]] = {
        "di": {},
        "co": {},
        "ir": {},
        "hr": {}
    }

    for ch in device_config.channels:
        addr = ch.modbus.address

        # 1. Основной регистр канала
        if ch.type == ChannelType.DI:
            init_val = 0
            if ch.simulation and ch.simulation.initial_value is not None:
                init_val = 1 if ch.simulation.initial_value else 0
            if ch.modbus.table == RegisterTable.DISCRETE_INPUTS:
                memory["di"][addr] = init_val
            else:
                memory["co"][addr] = init_val

        elif ch.type == ChannelType.DO:
            init_val = 0
            if ch.simulation and ch.simulation.initial_value is not None:
                init_val = 1 if ch.simulation.initial_value else 0
            memory["co"][addr] = init_val

        elif ch.type == ChannelType.AI:
            init_raw = 0
            if ch.physical_range and ch.physical_range.raw_min is not None:
                init_raw = ch.physical_range.raw_min
            if ch.simulation and ch.simulation.initial_value is not None:
                if ch.coefficients:
                    init_raw = ch.to_raw(
                        float(ch.simulation.initial_value),
                        ch.coefficients.k.default_value,
                        ch.coefficients.b.default_value
                    )
                else:
                    init_raw = int(ch.simulation.initial_value)

            target_table = "ir" if ch.modbus.table == RegisterTable.INPUT_REGISTERS else "hr"
            memory[target_table][addr] = max(0, min(65535, init_raw))

            # 2. Инициализация коэффициентов k и b в Holding Registers
            if ch.coefficients:
                # Регистры k (float32 занимает 2 регистра)
                k_regs = encode_float32(
                    ch.coefficients.k.default_value,
                    ch.coefficients.k.byte_order,
                    ch.coefficients.k.word_order
                )
                memory["hr"][ch.coefficients.k.address] = k_regs[0]
                memory["hr"][ch.coefficients.k.address + 1] = k_regs[1]

                # Регистры b (float32 занимает 2 регистра)
                b_regs = encode_float32(
                    ch.coefficients.b.default_value,
                    ch.coefficients.b.byte_order,
                    ch.coefficients.b.word_order
                )
                memory["hr"][ch.coefficients.b.address] = b_regs[0]
                memory["hr"][ch.coefficients.b.address + 1] = b_regs[1]

    return memory

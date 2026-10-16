"""
Пакет ModbusSlaveEmu: эмулятор ведомых устройств Modbus (Modbus Slave Device)
с генерацией синтетических данных и поддержкой математических функций.
"""

from ModbusSlaveEmu.device import (
    AuxSimulationConfig,
    BaseSignalGenerator,
    ChannelSimulationProfile,
    ConstantGenerator,
    CustomFunctionGenerator,
    EmuModbusSlaveDevice,
    GeneratorRegistry,
    LinearRampGenerator,
    RandomWalkGenerator,
    SineWaveGenerator,
    ToggleGenerator,
)
from ModbusSlaveEmu.manager import ModbusSlaveManager
from ModbusSlaveEmu.bridge import ModbusBridge

__all__ = [
    "EmuModbusSlaveDevice",
    "ModbusSlaveManager",
    "ModbusBridge",
    "AuxSimulationConfig",
    "ChannelSimulationProfile",
    "BaseSignalGenerator",
    "ConstantGenerator",
    "SineWaveGenerator",
    "RandomWalkGenerator",
    "LinearRampGenerator",
    "ToggleGenerator",
    "CustomFunctionGenerator",
    "GeneratorRegistry",
]

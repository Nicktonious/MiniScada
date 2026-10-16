"""
Подпакет device: эмуляция единичного Modbus-устройства с генерацией данных.
"""

from ModbusSlaveEmu.device.device import EmuModbusSlaveDevice
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
from ModbusSlaveEmu.device.schemas import (
    AuxSimulationConfig,
    ChannelSimulationProfile,
)

__all__ = [
    "EmuModbusSlaveDevice",
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

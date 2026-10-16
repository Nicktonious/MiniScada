#!/usr/bin/env python3
"""
Автоматизированные тесты для EmuModbusSlaveDevice и генераторов сигналов.
"""

import math
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ModbusSlaveEmu.device import (
    AuxSimulationConfig,
    ChannelSimulationProfile,
    ConstantGenerator,
    CustomFunctionGenerator,
    EmuModbusSlaveDevice,
    LinearRampGenerator,
    RandomWalkGenerator,
    SineWaveGenerator,
    ToggleGenerator,
)


class TestEmuModbusSlaveDevice(unittest.TestCase):
    def setUp(self):
        self.config_path = PROJECT_ROOT / "Common" / "device_config.example.json"
        self.device = EmuModbusSlaveDevice(config=self.config_path)

    def tearDown(self):
        if self.device.is_running:
            self.device.stop()

    def test_initialization(self):
        """Проверка загрузки устройства и начального состояния регистров."""
        self.assertEqual(self.device.config.device_id, "io_module_combo_01")
        self.assertEqual(self.device.config.slave_id, 1)

        # Проверка начальных карт регистров
        data_bank = self.device.get_data_bank_snapshot()
        self.assertIn(0, data_bank["di"])
        self.assertIn(0, data_bank["co"])
        self.assertIn(0, data_bank["ir"])
        self.assertIn(100, data_bank["hr"])
        self.assertIn(102, data_bank["hr"])

    def test_simulation_tick_and_calibration(self):
        """Проверка выполнения одного такта симуляции и расчета y = k*x + b."""
        # Выполняем такт
        snap = self.device.tick(t=10.0, dt=0.5)
        channels = snap["channels"]

        self.assertIn("ai_pipe_pressure", channels)
        ai_data = channels["ai_pipe_pressure"]

        # Диапазон давления 0..16 бар, АЦП 800..4000
        self.assertGreaterEqual(ai_data["raw"], 800)
        self.assertLessEqual(ai_data["raw"], 4000)
        self.assertGreaterEqual(ai_data["eng"], 0.0)
        self.assertLessEqual(ai_data["eng"], 16.0)
        self.assertAlmostEqual(ai_data["k"], 0.005, places=4)
        self.assertAlmostEqual(ai_data["b"], -4.0, places=1)

    def test_custom_math_function(self):
        """Проверка задания произвольной математической функции 'на лету'."""
        # Устанавливаем константную функцию y = 10.0 bar
        self.device.set_math_function("ai_pipe_pressure", lambda t, dt: 10.0)
        snap = self.device.tick(t=20.0, dt=0.5)
        ai_data = snap["channels"]["ai_pipe_pressure"]

        # y = 10.0 -> x = (10 - (-4)) / 0.005 = 14 / 0.005 = 2800
        self.assertAlmostEqual(ai_data["eng"], 10.0, places=3)
        self.assertEqual(ai_data["raw"], 2800)

    def test_generators(self):
        """Проверка изолированной работы генераторов сигналов."""
        # Constant
        c_gen = ConstantGenerator(value=5.0)
        self.assertEqual(c_gen.generate(t=0.0), 5.0)

        # SineWave
        s_gen = SineWaveGenerator(amplitude=2.0, offset=10.0, period_seconds=4.0)
        # при t=0 -> 10.0 + 2*sin(0) = 10.0
        self.assertAlmostEqual(s_gen.generate(t=0.0), 10.0)

        # Toggle
        t_gen = ToggleGenerator(initial_value=False, toggle_interval_seconds=1.0)
        v0 = t_gen.generate(t=0.0)
        self.assertEqual(v0, 0.0)
        v1 = t_gen.generate(t=1.5)
        self.assertEqual(v1, 1.0)

    def test_lifecycle(self):
        """Проверка фонового запуска Run() и останова Stop()."""
        self.assertFalse(self.device.is_running)
        self.device.Run(blocking=False)
        self.assertTrue(self.device.is_running)

        self.device.Stop()
        self.assertFalse(self.device.is_running)


if __name__ == "__main__":
    unittest.main()

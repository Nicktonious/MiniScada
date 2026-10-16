#!/usr/bin/env python3
"""
Автоматизированные тесты для ModbusSlaveManager.
"""

import math
import pathlib
import struct
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ModbusSlaveEmu.manager import ModbusSlaveManager


class TestModbusSlaveManager(unittest.TestCase):
    def setUp(self):
        self.devices_dir = PROJECT_ROOT / "Common" / "devices"
        self.manager = ModbusSlaveManager(config_dir=self.devices_dir)

    def tearDown(self):
        if self.manager.is_running:
            self.manager.stop()

    def test_load_devices(self):
        """Проверка загрузки N устройств и регистрации Slave ID."""
        slave_ids = self.manager.list_slave_ids()
        self.assertIn(1, slave_ids)
        self.assertIn(2, slave_ids)
        self.assertIn(3, slave_ids)
        self.assertEqual(len(slave_ids), 3)

        dev1 = self.manager.get_device(1)
        self.assertIsNotNone(dev1)
        self.assertEqual(dev1.config.device_id, "io_module_combo_01")

        dev3 = self.manager.get_device_by_id("pump_ctrl_module_03")
        self.assertIsNotNone(dev3)
        self.assertEqual(dev3.config.slave_id, 3)

    def test_observer(self):
        """Проверка паттерна Observer при тике устройств."""
        events = []

        def observer(slave_id, device_id, snapshot):
            events.append((slave_id, device_id, snapshot))

        self.manager.add_observer(observer)

        # Выполняем такт для slave 1
        dev1 = self.manager.get_device(1)
        dev1.tick(t=10.0, dt=0.5)

        self.assertGreaterEqual(len(events), 1)
        last_sid, last_did, last_snap = events[-1]
        self.assertEqual(last_sid, 1)
        self.assertEqual(last_did, "io_module_combo_01")
        self.assertIn("ai_pipe_pressure", last_snap["channels"])

        # Удаляем наблюдателя
        self.manager.remove_observer(observer)
        old_count = len(events)
        dev1.tick(t=11.0, dt=0.5)
        self.assertEqual(len(events), old_count)

    def test_read_write_registers(self):
        """Проверка чтения и записи регистров (Holding Registers)."""
        # Запись в HR 100 на Slave 1
        self.manager.write(slave_id=1, table="holding_registers", address=100, values=[12345, 54321])
        res = self.manager.read(slave_id=1, table="holding_registers", address=100, quantity=2)
        self.assertEqual(res, [12345, 54321])

    def test_read_write_coils(self):
        """Проверка чтения и записи бит (Coils)."""
        # Запись бит на Slave 3
        self.manager.write(slave_id=3, table="coils", address=0, values=[True, False])
        res = self.manager.read(slave_id=3, table="coils", address=0, quantity=2)
        self.assertEqual(res, [True, False])

        # Запись одиночного значения
        self.manager.write(slave_id=3, table="coils", address=1, values=True)
        res2 = self.manager.read(slave_id=3, table="coils", address=0, quantity=2)
        self.assertEqual(res2, [True, True])

    def test_read_write_bytes(self):
        """Проверка чтения и записи сырых байтов."""
        # Упаковываем float 12.34 в 4 байта
        test_val = 12.34
        packed = struct.pack(">f", test_val)

        # Пишем в Holding Registers 102 на Slave 1
        self.manager.write_bytes(slave_id=1, table="holding_registers", address=102, data=packed)

        # Читаем ровно 4 байта
        raw_read = self.manager.read_bytes(slave_id=1, table="holding_registers", address=102, quantity_bytes=4)
        self.assertEqual(len(raw_read), 4)
        recovered_val = struct.unpack(">f", raw_read)[0]
        self.assertAlmostEqual(recovered_val, test_val, places=4)

    def test_pymodbus_interface(self):
        """Проверка методов get_values / set_values (прямая совместимость с pymodbus)."""
        # FC 5 (Write Single Coil) на Slave 3
        self.manager.set_values(slave_id=3, fc=5, address=0, values=[True])
        # FC 1 (Read Coils)
        val = self.manager.get_values(slave_id=3, fc=1, address=0, count=1)
        self.assertEqual(val, [True])

        # FC 16 (Write Multiple Registers) на Slave 2
        self.manager.set_values(slave_id=2, fc=16, address=100, values=[111, 222])
        # FC 3 (Read Holding Registers)
        regs = self.manager.get_values(slave_id=2, fc=3, address=100, count=2)
        self.assertEqual(regs, [111, 222])

    def test_error_handling(self):
        """Проверка обработки некорректных параметров."""
        # Несуществующий slave_id
        with self.assertRaises(KeyError):
            self.manager.read(slave_id=999, table="coils", address=0)

        # Несуществующая таблица
        with self.assertRaises(ValueError):
            self.manager.read(slave_id=1, table="invalid_table", address=0)

    def test_lifecycle(self):
        """Проверка запуска и остановки потоков."""
        self.assertFalse(self.manager.is_running)
        self.manager.Run(blocking=False)
        self.assertTrue(self.manager.is_running)
        for dev in self.manager.get_all_devices():
            self.assertTrue(dev.is_running)

        self.manager.Stop()
        self.assertFalse(self.manager.is_running)
        for dev in self.manager.get_all_devices():
            self.assertFalse(dev.is_running)


if __name__ == "__main__":
    unittest.main()

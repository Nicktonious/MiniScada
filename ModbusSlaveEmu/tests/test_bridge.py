#!/usr/bin/env python3
"""
Автоматизированные тесты для модуля ModbusBridge и интеграции с pymodbus.
"""

import asyncio
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pymodbus.client import AsyncModbusTcpClient
from ModbusSlaveEmu import ModbusBridge, ModbusSlaveManager
from ModbusSlaveEmu.bridge.bus_collector import BusCollector, BusInfo
from ModbusSlaveEmu.bridge.context import ManagerServerContext


class TestModbusBridge(unittest.TestCase):
    def setUp(self):
        self.devices_dir = PROJECT_ROOT / "Common" / "devices"
        self.manager = ModbusSlaveManager(config_dir=self.devices_dir)

    def tearDown(self):
        if self.manager.is_running:
            self.manager.stop()

    def test_bus_collector(self):
        """Проверка автоматического обнаружения и группировки шин."""
        buses = BusCollector.collect_buses(self.manager)
        self.assertEqual(len(buses), 1)

        bus = buses[0]
        self.assertEqual(bus.bus_type, "tcp")
        self.assertEqual(bus.host, "127.0.0.1")
        self.assertEqual(bus.port, 5020)
        # Все 3 устройства сидят на одном TCP порту 5020
        self.assertEqual(sorted(bus.slave_ids), [1, 2, 3])
        self.assertEqual(len(bus.devices), 3)

    def test_manager_server_context(self):
        """Проверка работы кастомного контекста pymodbus без сети."""
        ctx = ManagerServerContext(self.manager)
        self.assertEqual(sorted(ctx.device_ids), [1, 2, 3])

        async def _test_context():
            # Чтение Coils (FC 01) с устройства 3
            coils = await ctx.async_getValues(device_id=3, func_code=1, address=0, count=2)
            self.assertEqual(coils, [False, False])

            # Запись Coil (FC 05) на устройство 3
            res_set = await ctx.async_setValues(device_id=3, func_code=5, address=0, values=[True])
            self.assertIsNone(res_set)

            # Проверка прочитанного значения
            coils_after = await ctx.async_getValues(device_id=3, func_code=1, address=0, count=2)
            self.assertEqual(coils_after, [True, False])

            # Запрос к несуществующему устройству (Slave ID 99)
            err = await ctx.async_getValues(device_id=99, func_code=1, address=0, count=1)
            self.assertNotEqual(err, [True])

        asyncio.run(_test_context())

    def test_bridge_tcp_network_roundtrip(self):
        """Проверка сетевого взаимодействия через реальный сокет pymodbus."""
        async def _test_network():
            bridge = self.manager.bridge(auto_start=True)
            self.assertTrue(bridge.is_running)
            await asyncio.sleep(0.3)

            client = AsyncModbusTcpClient("127.0.0.1", port=5020)
            conn = await client.connect()
            self.assertTrue(conn)

            try:
                # 1. Читаем DI со Slave ID 3
                di_res = await client.read_discrete_inputs(address=0, count=2, device_id=3)
                self.assertFalse(di_res.isError())
                self.assertEqual(di_res.bits[:2], [True, True])

                # 2. Пишем Coil на Slave ID 3
                w_res = await client.write_coil(address=1, value=True, device_id=3)
                self.assertFalse(w_res.isError())

                co_res = await client.read_coils(address=0, count=2, device_id=3)
                self.assertEqual(co_res.bits[1], True)

                # 3. Пишем Holding Register на Slave ID 1
                wr_res = await client.write_registers(address=100, values=[555, 666], device_id=1)
                self.assertFalse(wr_res.isError())

                hr_res = await client.read_holding_registers(address=100, count=2, device_id=1)
                self.assertEqual(hr_res.registers, [555, 666])

            finally:
                client.close()
                bridge.Stop()
                self.assertFalse(bridge.is_running)

        asyncio.run(_test_network())


if __name__ == "__main__":
    unittest.main()

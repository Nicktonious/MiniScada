#!/usr/bin/env python3
"""
Пример использования высокоуровневого шлюза ModbusBridge.

Демонстрирует:
1. Создание менеджера устройств из папки Common/devices.
2. Запуск bridge через manager.bridge():
   - Автоматическое обнаружение шин (Modbus TCP / RTU).
   - Запуск слушателей серверов pymodbus на соответствующих портах.
3. Подключение стороннего Modbus TCP клиента (pymodbus AsyncModbusTcpClient) по сети:
   - Чтение аналоговых и дискретных регистров с разных Slave ID (1, 2, 3).
   - Передача команд управления (запись Coils и Holding Registers).
   - Получение и валидация ответов сервера.
4. Корректный останов шлюза.
"""

import asyncio
import pathlib
import sys
import time

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pymodbus.client import AsyncModbusTcpClient
from Common.config_models import decode_float32, encode_float32
from ModbusSlaveEmu import ModbusSlaveManager


async def run_client_demo():
    devices_dir = PROJECT_ROOT / "Common" / "devices"
    print("=" * 65)
    print("1. Инициализация ModbusSlaveManager и запуск метода bridge()")
    print("=" * 65)

    manager = ModbusSlaveManager(config_dir=devices_dir, update_interval_ms=300)

    # Запуск bridge (автоматически собирает шины и запускает серверы pymodbus)
    bridge = manager.bridge(
        auto_start=True,
        on_request=lambda action, sid, fc, addr, val: print(
            f"    [PDU Audit] {action} -> Slave ID {sid}, FC {fc:02d}, Addr {addr:03d}, Data: {val}"
        ),
    )

    print("\nАктивные шины, обслуживаемые pymodbus:")
    for ep in bridge.list_endpoints():
        print(f"  * {ep}")

    # Даем серверу время на привязку сокета
    await asyncio.sleep(0.5)

    print("\n" + "=" * 65)
    print("2. Подключение внешнего клиента pymodbus к шине TCP 127.0.0.1:5020")
    print("=" * 65)

    client = AsyncModbusTcpClient("127.0.0.1", port=5020)
    connected = await client.connect()
    assert connected, "Не удалось подключиться к Modbus TCP серверу шлюза!"
    print("  -> Клиент успешно подключился к шлюзу!")

    # 2.1 Чтение с устройства 1 (Slave ID 1: Комбинированный модуль)
    print("\n--- Запрос 1: Чтение давления (IR[0]) со Slave ID 1 ---")
    rr1 = await client.read_input_registers(address=0, count=1, device_id=1)
    raw_pressure = rr1.registers[0]
    # Читаем коэффициенты k и b из Holding Registers
    hr_resp = await client.read_holding_registers(address=100, count=4, device_id=1)
    k = decode_float32([hr_resp.registers[0], hr_resp.registers[1]])
    b = decode_float32([hr_resp.registers[2], hr_resp.registers[3]])
    pressure = k * raw_pressure + b
    print(f"  [Ответ Slave 1]: Raw={raw_pressure} -> Давление = {pressure:.2f} bar (k={k:.4f}, b={b:.1f})")

    # 2.2 Чтение с устройства 2 (Slave ID 2: Модуль температур)
    print("\n--- Запрос 2: Чтение 2 температур (IR[0..1]) со Slave ID 2 ---")
    rr2 = await client.read_input_registers(address=0, count=2, device_id=2)
    print(f"  [Ответ Slave 2]: Температуры (сырые коды АЦП): {rr2.registers}")

    # 2.3 Чтение и запись дискретных сигналов на Slave ID 3 (Блок насосов)
    print("\n--- Запрос 3: Чтение дискретных входов DI со Slave ID 3 ---")
    di_resp = await client.read_discrete_inputs(address=0, count=2, device_id=3)
    print(f"  [Ответ Slave 3]: Готовность насосов: {di_resp.bits[:2]}")

    print("\n--- Запрос 4: Отправка команды на включение насоса 1 (Write Coil[0] = True) ---")
    await client.write_coil(address=0, value=True, device_id=3)
    coils_resp = await client.read_coils(address=0, count=2, device_id=3)
    print(f"  [Ответ Slave 3]: Состояние реле после команды: {coils_resp.bits[:2]} (Насос 1 запущен)")

    # 2.4 Запись новых калибровочных коэффициентов в Slave ID 1
    print("\n--- Запрос 5: Калибровка датчика (Write Holding Registers 102..103, b = -2.5 bar) ---")
    new_b_regs = encode_float32(-2.5)
    await client.write_registers(address=102, values=new_b_regs, device_id=1)
    # Проверяем, что эмулятор воспринял калибровку
    hr_check = await client.read_holding_registers(address=102, count=2, device_id=1)
    recovered_b = decode_float32(hr_check.registers)
    print(f"  [Ответ Slave 1]: Новое смещение нуля b = {recovered_b:.2f} bar")

    # Закрываем клиента
    client.close()

    print("\n" + "=" * 65)
    print("3. Остановка шлюза через bridge.Stop()")
    print("=" * 65)
    bridge.Stop()
    print("ModbusBridge успешно остановлен. Статус:", bridge.is_running)


def main():
    asyncio.run(run_client_demo())


if __name__ == "__main__":
    main()

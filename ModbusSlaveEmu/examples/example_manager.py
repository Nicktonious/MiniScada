#!/usr/bin/env python3
"""
Пример использования ModbusSlaveManager.

Демонстрирует:
1. Инициализацию N slave-устройств из папки с конфигурациями (Common/devices).
2. Наблюдение (Observer) за обновлениями всех slave-устройств в реальном времени.
3. Чтение данных из устройств:
   - по Slave ID, таблице/FC, начальному адресу и количеству;
   - с явным указанием количества байт (quantity_bytes);
   - через pymodbus-совместимый интерфейс get_values.
4. Запись данных в устройства:
   - запись регистров (включая калибровку float32 в Holding Registers);
   - запись бит в Coils (управление реле);
   - запись сырыми байтами (write_bytes);
   - через pymodbus-совместимый интерфейс set_values.
5. Управление общим жизненным циклом (Run, Stop).
"""

import pathlib
import struct
import sys
import time

# Обеспечиваем пути импорта
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Common.config_models import decode_float32, encode_float32
from ModbusSlaveEmu import ModbusSlaveManager


def main():
    devices_dir = PROJECT_ROOT / "Common" / "devices"
    print(f"================================================================")
    print(f"Инициализация ModbusSlaveManager из папки: {devices_dir}")
    print(f"================================================================")

    # 1. Инициализация менеджера N устройств
    manager = ModbusSlaveManager(
        config_dir=devices_dir,
        update_interval_ms=400,  # обновление всех устройств каждые 400 мс
    )

    print(f"\nЗагружено slave-устройств: {len(manager.get_all_devices())}")
    for sid in manager.list_slave_ids():
        dev = manager.get_device(sid)
        print(f"  -> Slave ID {sid}: '{dev.config.name}' (ID: {dev.config.device_id}, каналов: {len(dev.config.channels)})")

    # 2. Подключение наблюдателя (Observer) за обновлениями телеметрии
    tick_counter = 0

    def on_device_update(slave_id: int, device_id: str, snapshot: dict):
        nonlocal tick_counter
        tick_counter += 1
        # Логируем каждый 3-й такт, чтобы не перегружать консоль
        if tick_counter % 3 == 0:
            ch_info = []
            for cid, cdata in snapshot["channels"].items():
                eng_val = cdata.get("eng")
                unit = cdata.get("unit", "")
                ch_info.append(f"{cid}={eng_val}{unit}")
            print(f"  [Observer Event #{tick_counter:02d}] Slave {slave_id} ({device_id}): {', '.join(ch_info)}")

    manager.add_observer(on_device_update)

    # 3. Запуск фонового обновления всех устройств через Run()
    print("\n--- 1. Запуск Run() для всех устройств ---")
    manager.Run(blocking=False)
    time.sleep(1.0)

    # 4. Демонстрация интерфейса чтения
    print("\n--- 2. Чтение данных из устройств через унифицированный интерфейс ---")

    # 4.1 Чтение аналогового значения из Slave ID 1 (IR 0 - давление)
    ir_vals = manager.read(slave_id=1, table="input_registers", address=0, quantity=1)
    raw_x = ir_vals[0]
    # Читаем коэффициенты k и b из Holding Registers (адреса 100-103)
    hr_regs = manager.read(slave_id=1, table="holding_registers", address=100, quantity=4)
    k_val = decode_float32([hr_regs[0], hr_regs[1]])
    b_val = decode_float32([hr_regs[2], hr_regs[3]])
    eng_pressure = k_val * raw_x + b_val
    print(f"  [Slave 1 Read]: IR[0] = {raw_x} отсчетов -> Давление: {eng_pressure:.2f} bar (k={k_val:.4f}, b={b_val:.1f})")

    # 4.2 Чтение с явным указанием количества байт (quantity_bytes)
    # 2 регистра температуры из Slave 2 (IR 0..1) = 4 байта
    temp_bytes = manager.read_bytes(slave_id=2, table="input_registers", address=0, quantity_bytes=4)
    temp_words = [
        struct.unpack(">H", temp_bytes[0:2])[0],
        struct.unpack(">H", temp_bytes[2:4])[0]
    ]
    print(f"  [Slave 2 Read Bytes (4 байта)]: {temp_bytes.hex()} -> Слова: {temp_words}")

    # 4.3 Чтение через Modbus Function Code (FC 02 = Discrete Inputs) из Slave 3
    di_bits = manager.read(slave_id=3, table=2, address=0, quantity=2)
    print(f"  [Slave 3 Read FC 02 (DI)]: pump1_ready={di_bits[0]}, pump2_ready={di_bits[1]}")

    # 5. Демонстрация интерфейса записи
    print("\n--- 3. Запись данных в устройства через интерфейс менеджера ---")

    # 5.1 Запись одного дискретного выхода (Пуск насоса 1 на Slave 3)
    print("  -> Запись: Включение Coils[0] на Slave ID 3")
    manager.write(slave_id=3, table="coils", address=0, values=True)
    coil_val = manager.read(slave_id=3, table="coils", address=0, quantity=1)[0]
    print(f"  -> Проверка Coils[0] после записи: {coil_val} (Включен)")

    # 5.2 Запись сырыми байтами (установка калибровки в Slave ID 1)
    # Изменим смещение b на -1.5 bar (Holding Register 102, 2 байта x 2 = 4 байта float)
    new_b_bytes = struct.pack(">f", -1.5)
    print(f"  -> Запись сырыми байтами (write_bytes): новое b = -1.5 в HR[102] на Slave ID 1 (bytes: {new_b_bytes.hex()})")
    manager.write_bytes(slave_id=1, table="holding_registers", address=102, data=new_b_bytes)

    # Проверяем прочитанное значение
    hr_read_bytes = manager.read_bytes(slave_id=1, table="holding_registers", address=102, quantity_bytes=4)
    recovered_b = struct.unpack(">f", hr_read_bytes)[0]
    print(f"  -> Проверка прочитанного b из HR[102]: {recovered_b:.2f} bar")

    # 5.3 Взаимодействие через методы pymodbus (get_values / set_values)
    print("\n--- 4. Pymodbus-совместимый интерфейс (get_values / set_values) ---")
    # Включение реле 2 на Slave 3 через FC 5 (Write Single Coil)
    manager.set_values(slave_id=3, fc=5, address=1, values=[True])
    fc1_result = manager.get_values(slave_id=3, fc=1, address=0, count=2)
    print(f"  [pymodbus get_values FC 1 (Coils)]: {fc1_result} (Оба насоса включены)")

    time.sleep(1.0)

    # 6. Остановка всех устройств через Stop()
    print("\n--- 5. Остановка менеджера (Stop) ---")
    manager.Stop()
    print(f"Все slave-устройства остановлены. Активен: {manager.is_running}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Пример запуска и демонстрации возможностей EmuModbusSlaveDevice.

Демонстрирует:
1. Загрузку конфигурации устройства из JSON.
2. Инициализацию таблиц регистров Modbus (DI, CO, IR, HR).
3. Работу со вспомогательной схемой симуляции (AuxSimulationConfig).
4. Задание пользовательских математических функций для каналов.
5. Запуск Run(), периодическое обновление регистров и остановку Stop().
6. Подписку на поток событий для сохранения синтетических данных в БД/моки.
7. Динамическое изменение калибровочных коэффициентов k и b в Holding Registers.
"""

import math
import pathlib
import sys
import time

# Обеспечиваем пути импорта
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ModbusSlaveEmu import (
    AuxSimulationConfig,
    ChannelSimulationProfile,
    EmuModbusSlaveDevice,
)


def main():
    config_path = PROJECT_ROOT / "Common" / "device_config.example.json"
    print(f"Загрузка конфигурации устройства: {config_path}")

    # 1. Создание вспомогательной конфигурации (опционально)
    aux_config = AuxSimulationConfig(
        update_interval_ms=300,  # обновление каждые 300 мс
        channel_profiles={
            # Переопределим модель давления на случайное блуждание
            "ai_pipe_pressure": ChannelSimulationProfile(
                generator_type="random_walk",
                step_std=0.25,
                noise_amplitude=0.05,
            )
        },
    )

    # 2. Создание экземпляра эмулятора Modbus Slave
    device = EmuModbusSlaveDevice(
        config=config_path,
        aux_config=aux_config,
    )

    print(f"\n[Инициализация] Устройство: '{device.config.name}' (Slave ID: {device.config.slave_id})")
    print(f"[Начальные карты регистров]:")
    print(f"  DI: {device.data_bank['di']}")
    print(f"  CO: {device.data_bank['co']}")
    print(f"  IR: {device.data_bank['ir']}")
    print(f"  HR (k, b): {device.data_bank['hr']}")

    # 3. Подписка на события обновления (моделирование записи в БД / передачу в UI)
    tick_count = 0

    def on_telemetry(dev: EmuModbusSlaveDevice, snapshot: dict):
        nonlocal tick_count
        tick_count += 1
        channels = snapshot["channels"]
        ai = channels.get("ai_pipe_pressure", {})
        di = channels.get("di_pump_run_status", {})
        do = channels.get("do_pump_start_relay", {})
        print(
            f"  [Такт {tick_count:02d}] "
            f"DI: {di.get('raw')} (eng: {di.get('eng')}) | "
            f"DO: {do.get('raw')} (eng: {do.get('eng')}) | "
            f"AI: raw={ai.get('raw')} -> eng={ai.get('eng')} {ai.get('unit')} "
            f"(k={ai.get('k'):.4f}, b={ai.get('b'):.1f})"
        )

    device.add_subscriber(on_telemetry)

    # 4. Запуск фонового обновления через Run()
    print("\n--- 1. Запуск Run() (фоновый поток обновления регистров) ---")
    device.Run(blocking=False)
    time.sleep(1.2)

    # 5. Демонстрация задания произвольной математической функции "на лету"
    print("\n--- 2. Назначение пользовательской математической функции: y(t) = 8.0 + 3.0 * cos(2*t) ---")
    device.set_math_function(
        channel_id="ai_pipe_pressure",
        func=lambda t, dt: 8.0 + 3.0 * math.cos(2.0 * t),
        noise_amplitude=0.02,
    )
    time.sleep(1.2)

    # 6. Демонстрация динамической калибровки через Holding Registers
    # Изменим смещение нуля b со стандартных -4.0 на -2.0 в Holding Register 102
    print("\n--- 3. Эмуляция Modbus Write: изменение коэффициента b в Holding Register 102-103 ---")
    from Common.config_models import encode_float32
    new_b_regs = encode_float32(-2.0)
    device.set_register("hr", 102, new_b_regs[0])
    device.set_register("hr", 103, new_b_regs[1])
    print("  -> Записано новое значение b = -2.0 в Holding Registers 102, 103")
    time.sleep(1.0)

    # 7. Эмуляция включения дискретного выхода DO со стороны SCADA/UI
    print("\n--- 4. Эмуляция Modbus Write: включение реле DO (Coil 0) ---")
    device.set_register("co", 0, 1)
    time.sleep(0.6)

    # 8. Остановка устройства через Stop()
    print("\n--- 5. Остановка Stop() ---")
    device.Stop()
    print("Эмулятор успешно остановлен.")

    # 9. Финальный снимок регистров
    final_snapshot = device.get_data_bank_snapshot()
    print(f"\n[Финальное состояние регистров]:")
    print(f"  DI: {final_snapshot['di']}")
    print(f"  CO: {final_snapshot['co']}")
    print(f"  IR: {final_snapshot['ir']}")
    print(f"  HR: {final_snapshot['hr']}")


if __name__ == "__main__":
    main()

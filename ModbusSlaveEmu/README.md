# Модуль эмуляции Modbus Slave (ModbusSlaveEmu)

Пакет предназначен для эмуляции ведомых устройств Modbus (Slave / Server) на основе унифицированных конфигураций JSON, генерации синтетических данных и предоставления сетевых интерфейсов через библиотеку **pymodbus**.

Используется для:
* Тестирования и моков утилит опроса без физического оборудования.
* Наполнения баз данных (PostgreSQL / TimescaleDB) реалистичными временными рядами.
* Интеграции с Modbus TCP/RTU сервером на базе `pymodbus`.
* Отладки пользовательского интерфейса (React UI / мнемосхемы).

---

## 📁 Структура пакета

```
ModbusSlaveEmu/
├── __init__.py                 # Единая точка экспорта публичного API
├── README.md                   # Документация пакета
├── device/                     # [Модуль устройства]
│   ├── __init__.py             # Экспорт EmuModbusSlaveDevice и генераторов
│   ├── device.py               # Класс эмулятора отдельного устройства
│   ├── generators.py           # Математические модели сигналов (sine, random walk, ramp...)
│   └── schemas.py              # Вспомогательные схемы симуляции (AuxSimulationConfig)
├── manager/                    # [Модуль менеджера]
│   ├── __init__.py             # Экспорт ModbusSlaveManager
│   └── manager.py              # Класс менеджера пула N устройств
├── bridge/                     # [Модуль сетевого шлюза pymodbus]
│   ├── __init__.py             # Экспорт компонентов шлюза
│   ├── bridge.py               # Высокоуровневый координатор ModbusBridge
│   ├── bus_collector.py        # Сборщик и группировщик шин (BusCollector, BusInfo)
│   ├── context.py              # PDU-адаптер контекста данных (ManagerServerContext)
│   └── listener.py             # Слушатели TCP/RTU серверов (TcpBusListener, RtuBusListener)
├── examples/                   # [Примеры запуска]
│   ├── example_device.py       # Демонстрация работы одиночного устройства
│   ├── example_manager.py      # Демонстрация управления пулом устройств
│   └── example_bridge.py       # Демонстрация сетевого шлюза pymodbus
└── tests/                      # [Автоматизированные тесты]
    ├── __init__.py
    ├── test_device.py          # Тесты эмулятора устройства и математических моделей
    ├── test_manager.py         # Тесты менеджера (чтение, запись, байты, observer)
    └── test_bridge.py          # Тесты шлюза и сетевого обмена с pymodbus
```

---

## 🏢 1. Менеджер устройств: `ModbusSlaveManager`

Класс `ModbusSlaveManager` загружает $N$ slave-устройств из указанной папки с конфигурациями по схеме `config.schema.json` и обеспечивает наблюдение (Observer) и операции чтения/записи:

```python
from ModbusSlaveEmu import ModbusSlaveManager

# 1. Загрузка N устройств из папки с JSON-конфигурациями
manager = ModbusSlaveManager(config_dir="Common/devices")

# 2. Наблюдение (Observer) за обновлениями всех устройств
def on_update(slave_id: int, device_id: str, snapshot: dict):
    print(f"Device {device_id} (Slave {slave_id}) tick: {snapshot['channels']}")

manager.add_observer(on_update)

# 3. Запуск фоновой симуляции всех устройств
manager.Run()

# 4. Чтение данных:
# 4.1 Чтение 16-битных регистров
regs = manager.read(slave_id=1, table="input_registers", address=0, quantity=2)
# 4.2 Чтение с явным указанием количества байт
raw_bytes = manager.read_bytes(slave_id=2, table="holding_registers", address=100, quantity_bytes=4)
# 4.3 Pymodbus-совместимое чтение (get_values)
coils = manager.get_values(slave_id=3, fc=1, address=0, count=2)

# 5. Запись данных:
# 5.1 Запись значения в Coil
manager.write(slave_id=3, table="coils", address=0, values=True)
# 5.2 Запись сырых байтов (например, калибровки float32)
manager.write_bytes(slave_id=1, table="holding_registers", address=102, data=b'\xbf\xc0\x00\x00')
# 5.3 Pymodbus-совместимая запись (set_values)
manager.set_values(slave_id=3, fc=5, address=1, values=[True])

# 6. Остановка всех устройств
manager.Stop()
```

Запуск примера менеджера:
```bash
.venv/bin/python3 ModbusSlaveEmu/examples/example_manager.py
```

---

## 🌉 2. Сетевой шлюз `ModbusBridge`

Метод `manager.bridge()` автоматически запускает серверы pymodbus на портах, определенных в конфигурациях устройств:

```python
import asyncio
from pymodbus.client import AsyncModbusTcpClient
from ModbusSlaveEmu import ModbusSlaveManager

# 1. Запуск менеджера и открытие сетевого шлюза одной строкой
manager = ModbusSlaveManager(config_dir="Common/devices")
bridge = manager.bridge(auto_start=True)

# 2. Подключение внешнего клиента (SCADA, Backend-опросник) по сети
async def main():
    client = AsyncModbusTcpClient("127.0.0.1", port=5020)
    await client.connect()

    # Чтение давления со Slave ID 1 (FC 04 - Input Registers)
    pressure = await client.read_input_registers(address=0, count=1, device_id=1)
    print("Давление (raw):", pressure.registers[0])

    # Отправка команды включения насоса (FC 05 - Write Single Coil)
    await client.write_coil(address=0, value=True, device_id=3)

    client.close()
    bridge.Stop()

asyncio.run(main())
```

Запуск примера сетевого шлюза:
```bash
.venv/bin/python3 ModbusSlaveEmu/examples/example_bridge.py
```

---

## ⚙️ 3. Эмулятор устройства: `EmuModbusSlaveDevice`

Управляет отдельным ведомым устройством, его регистрами и математическими функциями:

```python
import math
import time
from ModbusSlaveEmu import EmuModbusSlaveDevice

device = EmuModbusSlaveDevice(config="Common/device_config.example.json")

# Динамическое назначение пользовательской математической функции
device.set_math_function(
    channel_id="ai_pipe_pressure",
    func=lambda t, dt: 10.0 + 3.0 * math.sin(0.5 * t),
    noise_amplitude=0.05
)

device.Run()
time.sleep(2.0)
device.Stop()
```

Запуск примера устройства:
```bash
.venv/bin/python3 ModbusSlaveEmu/examples/example_device.py
```

---

## 🧪 4. Запуск автоматизированных тестов

Все тесты изолированы в каталоге `ModbusSlaveEmu/tests/` и покрывают устройство, менеджер, шлюз и сетевое взаимодействие:

```bash
.venv/bin/python3 -m unittest discover -s ModbusSlaveEmu/tests -p "test_*.py"
```

/**
 * TypeScript интерфейсы и утилиты для конфигурации Modbus-устройств.
 * Служит Single Source of Truth для React UI и клиентских сервисов.
 */

export type ConnectionType = 'tcp' | 'rtu';

export type RegisterTable =
  | 'discrete_inputs'   // 1x (FC 02)
  | 'coils'             // 0x (FC 01, 05, 15)
  | 'input_registers'   // 3x (FC 04)
  | 'holding_registers'; // 4x (FC 03, 06, 16)

export type DataType =
  | 'bool'
  | 'uint16'
  | 'int16'
  | 'uint32'
  | 'int32'
  | 'float32';

export type Endianness = 'big' | 'little';

export type ChannelType = 'DI' | 'DO' | 'AI';

export type SimulationPattern =
  | 'constant'
  | 'sine'
  | 'random_walk'
  | 'linear_ramp'
  | 'toggle'
  | 'pulse';

export interface ModbusTcpSettings {
  /** IP-адрес или доменное имя Modbus TCP сервера */
  host: string;
  /** Порт (по умолчанию 502) */
  port: number;
  /** Таймаут ответа в мс */
  timeout_ms?: number;
}

export interface ModbusRtuSettings {
  /** Имя последовательного порта (/dev/ttyUSB0, COM3 и т.п.) */
  port: string;
  /** Скорость передачи (бод) */
  baudrate: number;
  /** Количество бит данных (7 или 8) */
  data_bits?: 7 | 8;
  /** Контроль четности: 'N' (None), 'E' (Even), 'O' (Odd) */
  parity?: 'N' | 'E' | 'O';
  /** Количество стоповых бит (1 или 2) */
  stop_bits?: 1 | 2;
  /** Таймаут ответа в мс */
  timeout_ms?: number;
}

export interface ConnectionConfig {
  /** Тип протокола: tcp или rtu */
  type: ConnectionType;
  /** Настройки для TCP подключения */
  tcp?: ModbusTcpSettings;
  /** Настройки для последовательного RTU подключения */
  rtu?: ModbusRtuSettings;
}

export interface PollingConfig {
  /** Включен ли автоматический опрос устройства */
  enabled?: boolean;
  /** Периодичность опроса текущих значений каналов (мс) */
  poll_interval_ms: number;
  /** Периодичность чтения калибровочных коэффициентов k и b из Holding Registers (мс) */
  coefficients_poll_interval_ms?: number;
  /** Максимальное число повторных попыток при ошибке связи */
  max_retries?: number;
}

export interface ModbusMapping {
  /** Таблица регистров Modbus */
  table: RegisterTable;
  /** 0-based адрес смещения регистра */
  address: number;
  /** Тип данных */
  data_type?: DataType;
  /** Порядок байтов */
  byte_order?: Endianness;
  /** Порядок слов (для 32-битных типов) */
  word_order?: Endianness;
}

export interface PhysicalRange {
  /** Минимальное инженерное значение физической величины */
  min_val: number;
  /** Максимальное инженерное значение физической величины */
  max_val: number;
  /** Единица измерения (bar, °C, mA, V, % и т.д.) */
  unit: string;
  /** Минимальный сырой код АЦП/регистра (по умолчанию 0) */
  raw_min?: number;
  /** Максимальный сырой код АЦП/регистра (по умолчанию 65535) */
  raw_max?: number;
}

export interface CoefficientRegister {
  /** Таблица регистров (всегда holding_registers) */
  table?: 'holding_registers';
  /** 0-based начальный адрес Holding Register */
  address: number;
  /** Тип данных (по умолчанию float32) */
  data_type?: DataType;
  /** Порядок байтов */
  byte_order?: Endianness;
  /** Порядок слов */
  word_order?: Endianness;
  /** Начальное значение коэффициента */
  default_value: number;
  /** Описание коэффициента */
  description?: string;
}

export interface AICoefficients {
  /** Коэффициент наклона / масштабирования (Slope / Gain) */
  k: CoefficientRegister;
  /** Аддитивное смещение нуля (Zero Offset / Bias) */
  b: CoefficientRegister;
}

export interface SimulationConfig {
  /** Алгоритм симуляции сигнала */
  pattern: SimulationPattern;
  /** Период цикла в секундах */
  period_seconds?: number;
  /** Амплитуда шума */
  noise_amplitude?: number;
  /** Начальное значение */
  initial_value?: number | boolean;
  /** Период переключения для DI в секундах */
  toggle_interval_seconds?: number;
}

export interface ChannelConfig {
  /** Уникальный строковый идентификатор канала */
  channel_id: string;
  /** Человекочитаемое имя */
  name: string;
  /** Подробное описание назначения канала */
  description?: string;
  /** Тип канала: DI, DO или AI */
  type: ChannelType;
  /** Параметры Modbus-адресации */
  modbus: ModbusMapping;
  /** Физический диапазон измерений (только для AI) */
  physical_range?: PhysicalRange;
  /** Коэффициенты калибровки y = k * x + b (обязательно для AI) */
  coefficients?: AICoefficients;
  /** Параметры симуляции для эмулятора */
  simulation?: SimulationConfig;
}

export interface ModbusDeviceConfig {
  /** Версия схемы (например, "1.0") */
  schema_version: '1.0';
  /** Уникальный идентификатор устройства */
  device_id: string;
  /** Имя устройства/модуля */
  name: string;
  /** Описание */
  description?: string;
  /** Modbus Slave / Unit ID (1..247) */
  slave_id: number;
  /** Настройки подключения */
  connection: ConnectionConfig;
  /** Настройки опроса */
  polling?: PollingConfig;
  /** Список каналов */
  channels: ChannelConfig[];
}

/**
 * Структура пакета телеметрии, передаваемого от backend к React UI по WebSocket / SSE
 */
export interface ChannelTelemetry {
  channel_id: string;
  timestamp: string; // ISO 8601
  raw_value: number | boolean;
  engineering_value: number | boolean;
  unit?: string;
  quality: 'GOOD' | 'BAD' | 'COMM_FAILURE';
}

/**
 * Преобразование сырого значения x в физическую инженерную величину y:
 * y = k * x + b
 */
export function calculateEngineeringValue(raw: number, k: number, b: number): number {
  return k * raw + b;
}

/**
 * Преобразование инженерной величины y обратно в сырой код регистра x:
 * x = (y - b) / k
 */
export function calculateRawValue(eng: number, k: number, b: number): number {
  if (Math.abs(k) < 1e-9) {
    return 0;
  }
  return Math.round((eng - b) / k);
}

"""
Модуль математических функций и генераторов сигналов для эмуляции
физических процессов в Modbus-устройствах (DI, DO, AI).
"""

from __future__ import annotations

import abc
import math
import random
import time
from typing import Any, Callable, Dict, Optional, Type, Union


class BaseSignalGenerator(abc.ABC):
    """
    Базовый абстрактный класс генератора сигнала.
    """

    def __init__(
        self,
        initial_value: float = 0.0,
        noise_amplitude: float = 0.0,
        min_val: Optional[float] = None,
        max_val: Optional[float] = None
    ) -> None:
        self.initial_value = float(initial_value)
        self.noise_amplitude = float(noise_amplitude)
        self.min_val = min_val
        self.max_val = max_val
        self.current_value = self.initial_value
        self.last_t: Optional[float] = None

    @abc.abstractmethod
    def _compute(self, t: float, dt: float) -> float:
        """
        Внутренний расчет математической функции от времени t и шага dt.
        """
        pass

    def generate(self, t: Optional[float] = None, dt: Optional[float] = None) -> float:
        """
        Генерирует очередное значение сигнала с учетом шума и физических ограничений.
        """
        now = time.monotonic() if t is None else t

        if dt is None:
            if self.last_t is not None and self.last_t < now:
                dt = now - self.last_t
            else:
                dt = 0.1
        self.last_t = now

        raw_val = self._compute(now, dt)

        # Добавление псевдослучайного шума
        if self.noise_amplitude > 0:
            noise = random.gauss(0.0, self.noise_amplitude / 2.0)
            raw_val += noise

        # Ограничение по границам диапазона
        if self.min_val is not None:
            raw_val = max(self.min_val, raw_val)
        if self.max_val is not None:
            raw_val = min(self.max_val, raw_val)

        self.current_value = raw_val
        return raw_val

    def reset(self) -> None:
        """Сброс генератора к начальному состоянию."""
        self.current_value = self.initial_value
        self.last_t = None


class ConstantGenerator(BaseSignalGenerator):
    """
    Генератор постоянного значения (с возможным гауссовым шумом).
    """

    def __init__(
        self,
        value: float = 0.0,
        noise_amplitude: float = 0.0,
        min_val: Optional[float] = None,
        max_val: Optional[float] = None
    ) -> None:
        super().__init__(
            initial_value=value,
            noise_amplitude=noise_amplitude,
            min_val=min_val,
            max_val=max_val
        )
        self.value = float(value)

    def _compute(self, t: float, dt: float) -> float:
        return self.value


class SineWaveGenerator(BaseSignalGenerator):
    """
    Генератор синусоидального колебания:
    y(t) = offset + amplitude * sin(2 * pi * t / period + phase)
    """

    def __init__(
        self,
        amplitude: float = 1.0,
        period_seconds: float = 10.0,
        offset: float = 0.0,
        phase: float = 0.0,
        noise_amplitude: float = 0.0,
        min_val: Optional[float] = None,
        max_val: Optional[float] = None
    ) -> None:
        super().__init__(
            initial_value=offset,
            noise_amplitude=noise_amplitude,
            min_val=min_val,
            max_val=max_val
        )
        self.amplitude = float(amplitude)
        self.period = max(0.001, float(period_seconds))
        self.offset = float(offset)
        self.phase = float(phase)
        self.t0: Optional[float] = None

    def _compute(self, t: float, dt: float) -> float:
        if self.t0 is None:
            self.t0 = t
        rel_t = t - self.t0
        omega = (2.0 * math.pi) / self.period
        return self.offset + self.amplitude * math.sin(omega * rel_t + self.phase)

    def reset(self) -> None:
        super().reset()
        self.t0 = None


class RandomWalkGenerator(BaseSignalGenerator):
    """
    Генератор случайного блуждания (Ornstein-Uhlenbeck / Bounded Random Walk).
    Идеально подходит для реалистичной имитации температуры, давления и уровня в емкости.
    """

    def __init__(
        self,
        initial_value: float = 0.0,
        step_std: float = 0.2,
        mean_target: Optional[float] = None,
        reversion_speed: float = 0.05,
        min_val: Optional[float] = None,
        max_val: Optional[float] = None,
        noise_amplitude: float = 0.0
    ) -> None:
        super().__init__(
            initial_value=initial_value,
            noise_amplitude=noise_amplitude,
            min_val=min_val,
            max_val=max_val
        )
        self.step_std = float(step_std)
        self.mean_target = self.initial_value if mean_target is None else float(mean_target)
        self.reversion_speed = float(reversion_speed)

    def _compute(self, t: float, dt: float) -> float:
        # Возврат к среднему (Mean reversion)
        drift = self.reversion_speed * (self.mean_target - self.current_value) * dt
        # Случайное приращение
        step = random.gauss(0.0, self.step_std * math.sqrt(max(0.01, dt)))
        new_val = self.current_value + drift + step
        return new_val


class LinearRampGenerator(BaseSignalGenerator):
    """
    Генератор пилообразного или треугольного сигнала (нарастание / спад).
    """

    def __init__(
        self,
        min_val: float = 0.0,
        max_val: float = 10.0,
        period_seconds: float = 10.0,
        sawtooth: bool = True,
        noise_amplitude: float = 0.0
    ) -> None:
        super().__init__(
            initial_value=min_val,
            noise_amplitude=noise_amplitude,
            min_val=min_val,
            max_val=max_val
        )
        self.period = max(0.001, float(period_seconds))
        self.sawtooth = sawtooth
        self.t0: Optional[float] = None

    def _compute(self, t: float, dt: float) -> float:
        if self.t0 is None:
            self.t0 = t
        span = (self.max_val or 10.0) - (self.min_val or 0.0)
        min_v = self.min_val or 0.0
        rel_t = (t - self.t0) % self.period
        fraction = rel_t / self.period

        if self.sawtooth:
            return min_v + fraction * span
        else:
            # Треугольная волна
            if fraction < 0.5:
                return min_v + (fraction * 2.0) * span
            else:
                return min_v + ((1.0 - fraction) * 2.0) * span

    def reset(self) -> None:
        super().reset()
        self.t0 = None


class ToggleGenerator(BaseSignalGenerator):
    """
    Дискретный генератор: периодическое или псевдослучайное переключение (0 / 1) для DI.
    """

    def __init__(
        self,
        initial_value: bool = False,
        toggle_interval_seconds: float = 5.0,
        random_factor: float = 0.0
    ) -> None:
        super().__init__(initial_value=1.0 if initial_value else 0.0)
        self.interval = max(0.1, float(toggle_interval_seconds))
        self.random_factor = max(0.0, min(1.0, float(random_factor)))
        self.last_toggle: Optional[float] = None
        self.current_state = bool(initial_value)
        self._next_interval = self._calculate_next_interval()

    def _calculate_next_interval(self) -> float:
        if self.random_factor > 0:
            jitter = (random.random() * 2.0 - 1.0) * self.random_factor * self.interval
            return max(0.2, self.interval + jitter)
        return self.interval

    def _compute(self, t: float, dt: float) -> float:
        if self.last_toggle is None:
            self.last_toggle = t
        if t - self.last_toggle >= self._next_interval:
            self.current_state = not self.current_state
            self.last_toggle = t
            self._next_interval = self._calculate_next_interval()
        return 1.0 if self.current_state else 0.0

    def reset(self) -> None:
        super().reset()
        self.last_toggle = None


class CustomFunctionGenerator(BaseSignalGenerator):
    """
    Генератор на основе произвольной пользовательской математической функции / корутины.
    Принимает функцию вида: f(t: float, dt: float) -> float.
    """

    def __init__(
        self,
        func: Callable[[float, float], float],
        initial_value: float = 0.0,
        noise_amplitude: float = 0.0,
        min_val: Optional[float] = None,
        max_val: Optional[float] = None
    ) -> None:
        super().__init__(
            initial_value=initial_value,
            noise_amplitude=noise_amplitude,
            min_val=min_val,
            max_val=max_val
        )
        self.func = func

    def _compute(self, t: float, dt: float) -> float:
        return float(self.func(t, dt))


# ============================================================================
# Реестр фабрик генераторов для динамической загрузки
# ============================================================================

class GeneratorRegistry:
    """
    Реестр фабрик генераторов сигналов для создания их по имени из схемы/конфигурации.
    """

    _registry: Dict[str, Type[BaseSignalGenerator]] = {
        "constant": ConstantGenerator,
        "sine": SineWaveGenerator,
        "random_walk": RandomWalkGenerator,
        "linear_ramp": LinearRampGenerator,
        "toggle": ToggleGenerator,
    }

    @classmethod
    def register(cls, name: str, generator_cls: Type[BaseSignalGenerator]) -> None:
        """Регистрирует новый класс генератора под строковым именем."""
        cls._registry[name.lower()] = generator_cls

    @classmethod
    def create(cls, name: str, **kwargs: Any) -> BaseSignalGenerator:
        """Создает экземпляр генератора по имени."""
        gen_cls = cls._registry.get(name.lower())
        if not gen_cls:
            raise ValueError(f"Неизвестный тип генератора: '{name}'. Доступны: {list(cls._registry.keys())}")
        return gen_cls(**kwargs)

"""
Вспомогательная схема конфигурации симуляции (Auxiliary Simulation Schema)
для расширенной настройки математических моделей, интервалов обновления
и сценариев генерации синтетических данных.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Union

try:
    from pydantic import BaseModel, Field
except ImportError:
    class _FieldInfo:
        def __init__(self, default=None, default_factory=None):
            self.default = default
            self.default_factory = default_factory

    def Field(*args, **kwargs):
        default = kwargs.get("default", None)
        default_factory = kwargs.get("default_factory", None)
        return _FieldInfo(default=default, default_factory=default_factory)

    class BaseModel:
        def __init__(self, **kwargs):
            # Заполняем дефолтные значения из объявлений класса
            for attr_name, attr_val in self.__class__.__dict__.items():
                if isinstance(attr_val, _FieldInfo):
                    if attr_val.default_factory is not None:
                        setattr(self, attr_name, attr_val.default_factory())
                    else:
                        setattr(self, attr_name, attr_val.default)
            for k, v in kwargs.items():
                setattr(self, k, v)

        @classmethod
        def model_validate(cls, data: Any):
            if isinstance(data, cls):
                return data
            if not isinstance(data, dict):
                return data
            return cls(**data)

        def model_dump(self) -> dict:
            return self.__dict__


class ChannelSimulationProfile(BaseModel):
    """
    Профиль симуляции отдельного канала.
    Позволяет переопределить математическую функцию генерации без модификации
    основного конфигурационного файла устройства.
    """
    generator_type: Optional[str] = Field(
        default=None,
        description="Тип генератора: sine, random_walk, linear_ramp, constant, toggle"
    )
    # Параметры мат. функции
    amplitude: Optional[float] = None
    period_seconds: Optional[float] = None
    offset: Optional[float] = None
    step_std: Optional[float] = None
    noise_amplitude: Optional[float] = None
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    initial_value: Optional[Union[float, bool]] = None
    toggle_interval_seconds: Optional[float] = None
    # Пользовательская функция (передается в Python-коде)
    custom_func: Optional[Callable[[float, float], float]] = None


class AuxSimulationConfig(BaseModel):
    """
    Вспомогательная конфигурация симулятора Modbus-устройства.
    """
    update_interval_ms: int = Field(
        default=500,
        description="Интервал обновления словаря регистров (в миллисекундах)"
    )
    random_seed: Optional[int] = Field(
        default=None,
        description="Зерно для воспроизводимой генерации синтетических данных"
    )
    channel_profiles: Dict[str, ChannelSimulationProfile] = Field(
        default_factory=dict,
        description="Словарь переопределения мат. функций: channel_id -> профиль симуляции"
    )

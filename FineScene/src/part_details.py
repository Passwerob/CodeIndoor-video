"""Reusable semantic detail recipes, independent of the containing furniture."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class BookDetail(BaseModel):
    model_config=ConfigDict(extra='forbid')
    orientation: Literal['upright','flat']='upright'
    palette_index: int=Field(default=0,ge=0,le=7)
    binding: Literal['classic','cloth','paperback','leather']='classic'


Finish=Literal['matte','wood','metal']


class SurfaceSpec(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    texture_id: str | None=None
    roughness: float=Field(default=.5,ge=.05,le=1)
    grain_axis: Literal['x','y','z']='z'
    texture_scale: float=Field(default=1,ge=.1,le=10)

    @field_validator('texture_id')
    @classmethod
    def known_texture(cls,value):
        from material_catalog import catalog
        if value is not None and value not in catalog():
            raise ValueError('Unknown local texture capability: '+value)
        return value

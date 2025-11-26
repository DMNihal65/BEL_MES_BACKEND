from pydantic import BaseModel

class ComponentQuantityIn(BaseModel):
    component: str
    quantity: int

class ComponentQuantityOut(BaseModel):
    component: str
    quantity: int
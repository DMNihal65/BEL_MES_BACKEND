from pydantic import BaseModel
from typing import Optional, List, Dict

class MPPSection(BaseModel):
    title: str
    instructions: str

class MPPInstructions(BaseModel):
    sections: List[Dict[str, str]]

class MPPResponse(BaseModel):
    id: int
    order_id: int
    operation_id: int
    document_id: Optional[int]
    fixture_number: str
    ipid_number: str
    datum_x: str
    datum_y: str
    datum_z: str
    work_instructions: dict
    part_number: str
    operation_number: int

    class Config:
        from_attributes = True

class NewMPPCreate(BaseModel):
    part_number: str
    operation_number: int
    fixture_number: str
    ipid_number: str
    datum_x: str
    datum_y: str
    datum_z: str
    work_instructions: List[MPPSection]

class UpdateMPPSections(BaseModel):
    work_instructions: List[MPPSection]

class MPPUpdateRequest(BaseModel):
    fixture_number: str
    ipid_number: str
    datum_x: str
    datum_y: str
    datum_z: str
    work_instructions: List[MPPSection]

class MPPUpdateResponse(BaseModel):
    id: int
    order_id: int
    operation_id: int
    document_id: Optional[int]
    fixture_number: str
    ipid_number: str
    datum_x: str
    datum_y: str
    datum_z: str
    work_instructions: dict

    class Config:
        from_attributes = True



from pydantic import BaseModel
from typing import Optional, List, Dict

class MPPSection(BaseModel):
    title: str
    instructions: str

class MPPInstructions(BaseModel):
    sections: List[Dict[str, str]]

class MPPResponse(BaseModel):
    id: int
    order_id: int
    operation_id: int
    document_id: Optional[int]
    fixture_number: str
    ipid_number: str
    datum_x: str
    datum_y: str
    datum_z: str
    work_instructions: dict
    part_number: str
    operation_number: int

    class Config:
        from_attributes = True

class NewMPPCreate(BaseModel):
    part_number: str
    operation_number: int
    fixture_number: str
    ipid_number: str
    datum_x: str
    datum_y: str
    datum_z: str
    work_instructions: List[MPPSection]

class UpdateMPPSections(BaseModel):
    work_instructions: List[MPPSection]

class MPPUpdateRequest(BaseModel):
    fixture_number: str
    ipid_number: str
    datum_x: str
    datum_y: str
    datum_z: str
    work_instructions: List[MPPSection]

class MPPUpdateResponse(BaseModel):
    id: int
    order_id: int
    operation_id: int
    document_id: Optional[int]
    fixture_number: str
    ipid_number: str
    datum_x: str
    datum_y: str
    datum_z: str
    work_instructions: dict

    class Config:
        from_attributes = True



"""Actual Pydantic v2 strict contracts; no free-text mail field exists."""
import re
from decimal import Decimal
from typing import Annotated, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value):
        raise ValueError('invalid_identifier')
    return value

def decimal_value(value):
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,4})?', value):
        raise ValueError('noncanonical_decimal')
    if Decimal(value) <= 0:
        raise ValueError('nonpositive_value')
    return format(Decimal(value).normalize(),'f')

class Scope(Contract):
    factory_id: str
    customer_id: str
    product_id: str
    order_id: str
    quantity: Optional[int] = Field(default=None, ge=1, le=1000000)
    _ids = field_validator('factory_id','customer_id','product_id', 'order_id')(identifier)

class Reference(Contract):
    source_id: str
    version: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    _id = field_validator('source_id')(identifier)
    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start:
            raise ValueError('invalid_location')
        return self

class CategoryValue(Contract):
    kind: Literal['category']
    value: Literal['gear_fixture', 'cnc_fixture', 'steel', 'aluminum', 'turning', 'milling', 'grinding']

class MeasurementValue(Contract):
    kind: Literal['measurement']
    amount: str
    unit: Literal['day', 'mm']
    _decimal = field_validator('amount')(decimal_value)

class MoneyValue(Contract):
    kind: Literal['money']
    amount: str
    currency: Literal['USD', 'EUR', 'CNY']
    quantity: int = Field(ge=1, le=1000000)
    unit: Literal['piece']
    basis: Literal['total', 'per_piece']
    _decimal = field_validator('amount')(decimal_value)

class CertificationValue(Contract):
    kind: Literal['certification']
    standard: Literal['ISO9001', 'ISO14001']

class CapabilityValue(Contract):
    kind: Literal['capability']
    value: Literal['manufacturing_feasibility']

Value = Annotated[Union[CategoryValue, MeasurementValue, MoneyValue, CertificationValue, CapabilityValue], Field(discriminator='kind')]
Slot = Literal['product', 'material', 'process', 'price', 'lead_time', 'certification', 'accuracy', 'capability']

def compatible(slot, value):
    expected = {'product':'category', 'material':'category', 'process':'category',
                'price':'money', 'lead_time':'measurement', 'accuracy':'measurement',
                'certification':'certification', 'capability':'capability'}
    if value.kind != expected[slot]:
        raise ValueError('slot_value_mismatch')
    if slot in ('product', 'material', 'process'):
        allowed = {'product':('gear_fixture','cnc_fixture'), 'material':('steel','aluminum'), 'process':('turning','milling','grinding')}
        if value.value not in allowed[slot]:
            raise ValueError('slot_value_mismatch')
    if slot in ('lead_time', 'accuracy') and value.unit != {'lead_time':'day','accuracy':'mm'}[slot]:
        raise ValueError('slot_unit_mismatch')

class FactoryFact(Contract):
    kind: Literal['factory_fact']
    slot: Literal['product', 'material']
    value: Value
    reference: Reference
    @model_validator(mode='after')
    def check_slot(self):
        compatible(self.slot, self.value)
        return self

class CustomerQuote(Contract):
    kind: Literal['customer_quote']
    slot: Literal['product', 'material', 'price', 'lead_time', 'certification', 'accuracy', 'capability']
    value: Value
    reference: Reference
    @model_validator(mode='after')
    def check_slot(self):
        compatible(self.slot, self.value)
        return self

class Inference(Contract):
    kind: Literal['inference']
    code: Literal['potential_product_fit', 'drawing_needed', 'process_unknown', 'process_grinding_candidate', 'process_milling_candidate', 'process_turning_candidate']
    support_refs: List[Reference] = Field(default_factory=list, max_length=4)

class PendingCommitment(Contract):
    kind: Literal['pending_commitment']
    slot: Literal['price', 'lead_time', 'certification', 'accuracy', 'capability']
    value: Optional[Value] = None
    reference: Optional[Reference] = None
    @model_validator(mode='after')
    def check_slot(self):
        if self.value is not None:
            compatible(self.slot, self.value)
        return self

Claim = Annotated[Union[FactoryFact, CustomerQuote, Inference, PendingCommitment], Field(discriminator='kind')]
Intent = Literal['clarify_drawing', 'clarify_quantity', 'clarify_price', 'clarify_lead_time', 'clarify_certification', 'clarify_accuracy', 'clarify_process']

class ModelEnvelope(Contract):
    schema_version: Literal['deal-intent-v1']
    claims: List[Claim] = Field(max_length=12)
    intents: List[Intent] = Field(max_length=7)
    @model_validator(mode='after')
    def nonempty(self):
        if not self.claims and not self.intents:
            raise ValueError('empty_intent')
        if len(set(self.intents)) != len(self.intents):
            raise ValueError('duplicate_intent')
        return self

class SourceAtom(Contract):
    role: Literal['catalog_fact','customer_requirement','quoted_statement']
    review: Literal['confirmed','pending']
    slot: Slot
    value: Value
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=1024)
    @model_validator(mode='after')
    def check_atom(self):
        compatible(self.slot,self.value)
        if self.end<=self.start:
            raise ValueError('invalid_location')
        return self

class SourceInput(Contract):
    source_id: str
    issuer_id: str
    version: int = Field(ge=1)
    owner: Literal['factory','customer']
    status: Literal['active','disabled','expired','conflict']
    review: Literal['reviewed','pending']
    product_id: str
    order_id: Optional[str] = None
    quantity: Optional[int] = Field(default=None,ge=1,le=1000000)
    valid_until: Optional[int] = None
    body: str = Field(min_length=1,max_length=10000)
    atoms: List[SourceAtom] = Field(min_length=1,max_length=30)
    _ids = field_validator('source_id','issuer_id','product_id')(identifier)
    @field_validator('order_id')
    @classmethod
    def order_identifier(cls,value):
        return None if value is None else identifier(value)
    @model_validator(mode='after')
    def checked_atoms(self):
        seen=set()
        for atom in self.atoms:
            if self.body[atom.start:atom.end]!=atom.quote:
                raise ValueError('atom_location_mismatch')
            pair=(atom.start,atom.end)
            if pair in seen:
                raise ValueError('duplicate_atom_location')
            seen.add(pair)
            if atom.value.kind=='money' and self.quantity is not None and atom.value.quantity!=self.quantity:
                raise ValueError('source_quantity_mismatch')
        if self.owner=='customer' and self.order_id is None:
            raise ValueError('customer_order_required')
        return self

class ApprovalRecord(Contract):
    draft_hash: str = Field(min_length=64,max_length=64)
    source_manifest_hash: str = Field(min_length=64,max_length=64)
    policy_version: str
    policy_digest: str = Field(min_length=64,max_length=64)
    revision: int = Field(ge=1)
    scope_hash: str = Field(min_length=64,max_length=64)
    actor: str
    actor_revision: int = Field(ge=1)
    audit_id: int = Field(ge=1)
    _ids = field_validator('actor','policy_version')(identifier)

class OperatorRecord(Contract):
    id: str
    roles: List[Literal['review','export','source_write','policy_write']] = Field(max_length=4)
    revision: int = Field(ge=1)
    _id = field_validator('id')(identifier)

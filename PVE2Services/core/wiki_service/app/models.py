from typing import List, Optional

from pydantic import BaseModel

from .store import Page as Page  # noqa: F401 — re-export
from .store import PageCreate as PageCreate


class NetworkInfo(BaseModel):
    name: str
    type: Optional[str]
    status: Optional[str]
    address: Optional[str]
    mac: Optional[str]
    mtu: Optional[int]
    active: Optional[bool]


class PCIInfo(BaseModel):
    id: Optional[str]
    vendor: Optional[str]
    vendor_name: Optional[str]
    device: Optional[str]
    device_name: Optional[str]
    class_: Optional[str]
    iommu_group: Optional[int]
    subsystem_device: Optional[str]


class NodeHardwareInfo(BaseModel):
    networks: List[NetworkInfo] = []
    pci: List[PCIInfo] = []


class PVEEntity(BaseModel):
    id: str
    name: str
    type: Optional[str]
    status: Optional[str]
    node: Optional[str]
    cores: Optional[int]
    memory_mb: Optional[int]
    total_disk: Optional[int]
    ip_address: Optional[str]


class StorageHistory(BaseModel):
    node: str
    storage_name: str
    total_bytes: int
    used_bytes: int
    content_types: str
    timestamp: str


class HealthHistory(BaseModel):
    component: str
    item_id: str
    status: str
    details: Optional[str]
    timestamp: str

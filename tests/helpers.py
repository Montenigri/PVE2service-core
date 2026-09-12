"""Test helpers and mock data factories."""

from typing import Any


def make_machine(
    machine_id: str = "100",
    name: str = "test-vm",
    node: str = "pve1",
    status: str = "running",
    machine_type: str = "qemu",
    cpu: float = 0.5,
    maxcpu: int = 2,
    mem: int = 1024000000,
    maxmem: int = 2048000000,
    disk: int = 32000000000,
    maxdisk: int = 64000000000,
    uptime: int = 86400,
    ip: str = "192.168.1.100",
    tags: str = "",
    **kwargs,
) -> dict[str, Any]:
    """Create a mock machine dict matching PVE2Core format."""
    return {
        "machine_id": machine_id,
        "name": name,
        "node": node,
        "status": status,
        "type": machine_type,
        "cpu": cpu,
        "maxcpu": maxcpu,
        "mem": mem,
        "maxmem": maxmem,
        "disk": disk,
        "maxdisk": maxdisk,
        "uptime": uptime,
        "ip": ip,
        "tags": tags,
        **kwargs,
    }


def make_node(
    node: str = "pve1",
    status: str = "online",
    cpu: float = 0.25,
    maxcpu: int = 8,
    mem: int = 8000000000,
    maxmem: int = 16000000000,
    disk: int = 100000000000,
    maxdisk: int = 500000000000,
    uptime: int = 604800,
    **kwargs,
) -> dict[str, Any]:
    """Create a mock node dict matching Proxmox API format."""
    return {
        "node": node,
        "status": status,
        "cpu": cpu,
        "cpus": maxcpu,
        "mem": mem,
        "maxmem": maxmem,
        "disk": disk,
        "maxdisk": maxdisk,
        "uptime": uptime,
        **kwargs,
    }


def make_storage(
    storage: str = "local-lvm",
    node: str = "pve1",
    type: str = "lvmthin",
    total: int = 100000000000,
    used: int = 50000000000,
    content: str = "images,rootfs",
    **kwargs,
) -> dict[str, Any]:
    """Create a mock storage dict matching Proxmox API format."""
    return {
        "storage": storage,
        "node": node,
        "type": type,
        "total": total,
        "used": used,
        "content": content,
        **kwargs,
    }


def make_dns_record(
    machine_id: str = "100",
    machine_name: str = "test-vm",
    hostname: str = "test-vm",
    ip_address: str = "192.168.1.100",
    enabled: bool = True,
    **kwargs,
) -> dict[str, Any]:
    """Create a mock DNS record dict."""
    return {
        "machine_id": machine_id,
        "machine_name": machine_name,
        "hostname": hostname,
        "ip_address": ip_address,
        "enabled": enabled,
        **kwargs,
    }


def make_power_schedule(
    machine_id: str = "100",
    machine_name: str = "test-vm",
    enabled: bool = True,
    weekday_start_time: str = "08:00",
    weekday_stop_time: str = "18:00",
    weekend_start_time: str = "10:00",
    weekend_stop_time: str = "20:00",
    **kwargs,
) -> dict[str, Any]:
    """Create a mock power schedule dict."""
    return {
        "machine_id": machine_id,
        "machine_name": machine_name,
        "enabled": enabled,
        "weekday_start_time": weekday_start_time,
        "weekday_stop_time": weekday_stop_time,
        "weekend_start_time": weekend_start_time,
        "weekend_stop_time": weekend_stop_time,
        "monday": True,
        "tuesday": True,
        "wednesday": True,
        "thursday": True,
        "friday": True,
        "saturday": False,
        "sunday": False,
        "force_stop_timeout": 5,
        **kwargs,
    }


def make_proxy_mapping(
    machine_id: str = "100",
    machine_name: str = "test-vm",
    hostname: str = "app",
    domain: str = "example.com",
    ip_address: str = "192.168.1.100",
    port: int = 80,
    enabled: bool = True,
    **kwargs,
) -> dict[str, Any]:
    """Create a mock proxy mapping dict."""
    return {
        "machine_id": machine_id,
        "machine_name": machine_name,
        "hostname": hostname,
        "domain": domain,
        "ip_address": ip_address,
        "port": port,
        "websocket": False,
        "cache": False,
        "rate_limit": 0,
        "ssl_enabled": False,
        "extra_directives": "",
        "enabled": enabled,
        **kwargs,
    }

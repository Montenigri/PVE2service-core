"""A lightweight async Proxmox client ported from the Go implementation.

This implements the core read-only endpoints used by the wiki service:
- nodes
- node hardware (networks, pci)
- vms / lxc
- storage

The client is intentionally minimal and returns Python dicts/lists compatible
with the Pydantic models in `models.py`.
"""
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from .db import get_settings

logger = logging.getLogger("proxmox_adapter")


class ProxmoxClient:
    def __init__(self, base_url: str, token_id: str = None, secret: str = None, insecure: bool = False, timeout: int = 30):
        if not base_url:
            raise ValueError("base_url is required")
        if not base_url.startswith("http://") and not base_url.startswith("https://"):
            base_url = "http://" + base_url
        self.base_url = base_url.rstrip("/")
        self.token_id = token_id
        self.secret = secret
        self.insecure = insecure
        self._redirected = False
        # Redirects are followed manually in _request: httpx strips the
        # Authorization header when a redirect changes scheme (http -> https),
        # which made every call fail with 401 against pveproxy.
        self._client = httpx.AsyncClient(timeout=timeout, verify=not insecure, follow_redirects=False)

    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        url = f"{self.base_url}/api2/json{path}"
        headers = {}
        if self.token_id and self.secret:
            headers["Authorization"] = f"PVEAPIToken={self.token_id}={self.secret}"

        resp = await self._client.request(method, url, headers=headers, json=json)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location", "")
            parsed = urlparse(loc)
            target = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None
            if target and target != self.base_url and not self._redirected:
                logger.info("Proxmox base URL %s redirects to %s — retrying there", self.base_url, target)
                self.base_url = target
                self._redirected = True
                return await self._request(method, path, json=json)
        if resp.status_code == 403:
            raise RuntimeError(f"proxmox API forbidden (403) - check token permissions for path: {path}")
        if resp.status_code != 200:
            raise RuntimeError(f"proxmox API returned status {resp.status_code} for path {path}: {resp.text}")
        body = resp.json()
        return body.get("data")

    async def get_nodes(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/nodes")
        nodes: List[Dict[str, Any]] = []
        if not data:
            return nodes
        for n in data:
            hw = await self.get_node_hardware_info(n.get("node"))
            nodes.append({
                "id": n.get("node"),
                "name": n.get("node"),
                "type": "node",
                "status": n.get("status"),
                "memory_mb": int(n.get("maxmem", 0) / 1024 / 1024) if n.get("maxmem") else None,
                # Fields consumed by the wiki node template and dashboards
                "maxcpu": n.get("maxcpu") or 0,
                "cpu_load": n.get("cpu") or 0,
                "uptime_sec": n.get("uptime") or 0,
                "hardware_info": hw,
            })
        return nodes

    async def get_node_hardware_info(self, node: str) -> Dict[str, Any]:
        networks = await self.get_network_info(node)
        pci = await self.get_pci_info(node)
        return {"networks": networks, "pci": pci}

    async def get_network_info(self, node: str) -> List[Dict[str, Any]]:
        try:
            data = await self._request("GET", f"/nodes/{node}/network")
        except Exception as e:
            logger.exception("Error getting network info for %s: %s", node, e)
            return []
        out = []
        for item in data:
            out.append({
                "name": item.get("iface"),
                "type": item.get("type"),
                "status": item.get("status"),
                "address": item.get("address"),
                "mac": item.get("mac"),
                "mtu": item.get("mtu"),
                "active": item.get("active") == 1,
            })
        return out

    async def get_pci_info(self, node: str) -> List[Dict[str, Any]]:
        try:
            data = await self._request("GET", f"/nodes/{node}/hardware/pci")
        except Exception as e:
            logger.exception("Error getting pci info for %s: %s", node, e)
            return []
        out = []
        for item in data:
            out.append({
                "id": item.get("id"),
                "vendor": item.get("vendor"),
                "vendor_name": item.get("vendor_name"),
                "device": item.get("device"),
                "device_name": item.get("device_name"),
                "class_": item.get("class"),
                "iommu_group": item.get("iommugroup"),
                "subsystem_device": item.get("subsystem_device"),
            })
        return out

    async def get_vms(self) -> List[Dict[str, Any]]:
        nodes = await self.get_nodes()
        all_vms: List[Dict[str, Any]] = []
        for node in nodes:
            node_name = node.get("name")
            try:
                data = await self._request("GET", f"/nodes/{node_name}/qemu")
            except Exception as e:
                logger.exception("Error fetching qemu list for %s: %s", node_name, e)
                continue
            for vm in data:
                all_vms.append({
                    "id": f"vm-{vm.get('vmid')}",
                    "name": vm.get("name"),
                    "type": "vm",
                    "status": vm.get("status"),
                    "node": node_name,
                    "cores": vm.get("cpus"),
                    "memory_mb": int(vm.get("maxmem", 0) / 1024 / 1024) if vm.get("maxmem") else None,
                })
        return all_vms

    async def get_lxcs(self) -> List[Dict[str, Any]]:
        nodes = await self.get_nodes()
        all_lxcs: List[Dict[str, Any]] = []
        for node in nodes:
            node_name = node.get("name")
            try:
                data = await self._request("GET", f"/nodes/{node_name}/lxc")
            except Exception as e:
                logger.exception("Error fetching lxc list for %s: %s", node_name, e)
                continue
            for lxc in data:
                all_lxcs.append({
                    "id": f"lxc-{lxc.get('vmid')}",
                    "name": lxc.get("name"),
                    "type": "lxc",
                    "status": lxc.get("status"),
                    "node": node_name,
                    "cores": lxc.get("cpus"),
                    "memory_mb": int(lxc.get("maxmem", 0) / 1024 / 1024) if lxc.get("maxmem") else None,
                })
        return all_lxcs

    async def get_storage(self) -> List[Dict[str, Any]]:
        nodes = await self.get_nodes()
        all_storage: List[Dict[str, Any]] = []
        for node in nodes:
            node_name = node.get("name")
            try:
                data = await self._request("GET", f"/nodes/{node_name}/storage")
            except Exception as e:
                logger.exception("Error fetching storage list for %s: %s", node_name, e)
                continue
            for st in data:
                enabled = st.get("enabled", True)
                if isinstance(enabled, (int, float)) and enabled == 0:
                    continue
                total = st.get("total") or 0
                used = st.get("used") or 0
                all_storage.append({
                    "id": f"storage-{node_name}-{st.get('storage')}",
                    "name": st.get("storage"),
                    "type": "storage",
                    "status": "active",
                    "node": node_name,
                    "total_disk": st.get("total"),
                    "used_disk": st.get("used"),
                    # GB convenience fields for the wiki storage template
                    "total_gb": total // (1024 ** 3) if total else 0,
                    "used_gb": used // (1024 ** 3) if used else 0,
                    "memory_mb": int(total / 1024 / 1024) if total else None,
                    "content": st.get("content"),
                })
        return all_storage

    async def get_disks(self) -> List[Dict[str, Any]]:
        nodes = await self.get_nodes()
        all_disks: List[Dict[str, Any]] = []
        for node in nodes:
            node_name = node.get("name")
            try:
                data = await self._request("GET", f"/nodes/{node_name}/disks/list")
            except Exception as e:
                logger.exception("Error fetching disks list for %s: %s", node_name, e)
                continue
            for disk in data:
                smart = disk.get("smart") if isinstance(disk.get("smart"), dict) else {}
                all_disks.append({
                    "id": f"disk-{node_name}-{disk.get('devpath')}",
                    "name": disk.get("devpath"),
                    "type": "disk",
                    "status": smart.get("status") or disk.get("health"),
                    "node": node_name,
                    "total_disk": disk.get("size"),
                    "memory_mb": int(disk.get("size", 0) / 1024 / 1024) if disk.get("size") else None,
                    # Extra details for the wiki disk pages
                    "model": disk.get("model"),
                    "serial": disk.get("serial"),
                    "disk_type": disk.get("type"),
                    "size_bytes": disk.get("size"),
                    "wearout": disk.get("wearout"),
                })
        return all_disks

    async def get_ceph_health(self) -> Optional[Dict[str, Any]]:
        try:
            data = await self._request("GET", "/cluster/ceph/status")
        except Exception as e:
            logger.exception("Error fetching ceph health: %s", e)
            return None
        if not isinstance(data, dict):
            return {"id": "ceph-cluster", "name": "Ceph Cluster", "status": None}
        health = data.get("health", {}) or {}
        osdmap = ((data.get("osdmap") or {}).get("osdmap") or {})
        mons = ((data.get("monmap") or {}).get("mons") or [])
        return {
            "id": "ceph-cluster",
            "name": "Ceph Cluster",
            "status": health.get("status"),
            "osd_total": osdmap.get("num_osds") or 0,
            "osd_in": osdmap.get("num_in_osds") or 0,
            "osd_up": osdmap.get("num_up_osds") or 0,
            "mon_count": len(mons),
            "details": "; ".join(
                s.get("summary", {}).get("message", "")
                for s in (health.get("checks") or {}).values()
                if isinstance(s, dict)
            ),
        }

    async def get_cluster_summary(self) -> Optional[Dict[str, Any]]:
        """Single cluster entity (quorum + member count) for documentation pages."""
        try:
            data = await self._request("GET", "/cluster/status")
        except Exception as e:
            logger.exception("Error fetching cluster summary: %s", e)
            return None
        members = data.get("members", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        node_members = [m for m in members if isinstance(m, dict) and m.get("type") == "node"]
        quorate = bool(data.get("quorate")) if isinstance(data, dict) else all(
            m.get("online", 0) == 1 for m in node_members
        )
        name = data.get("name") if isinstance(data, dict) else None
        return {
            "id": f"cluster-{name}" if name else "cluster-default",
            "name": name or "Proxmox Cluster",
            "type": "cluster",
            "status": "quorate" if quorate else "no-quorum",
            "quorate": "yes" if quorate else "no",
            "nodes_count": len(node_members),
            "nodes_online": sum(1 for m in node_members if m.get("online", 0) == 1),
        }

    async def get_rrd_data(self, node: str, vmid: str, type: str = "qemu",
                            timeframe: str = "hour") -> List[Dict[str, Any]]:
        """Return time-series RRD data for a VM or LXC.

        Args:
            node: Proxmox node name.
            vmid: VM or LXC ID.
            type: "qemu" (VM) or "lxc" (container).
            timeframe: "hour", "day", "week", "month", "year".
        """
        try:
            return await self._request("GET", f"/nodes/{node}/{type}/{vmid}/rrddata?timeframe={timeframe}")
        except Exception as e:
            logger.exception("Error fetching RRD data for %s/%s/%s: %s", node, type, vmid, e)
            return []

    async def agent_network_interfaces(self, node: str, vmid: str) -> List[str]:
        """Return IPv4 addresses from QEMU Guest Agent."""
        try:
            data = await self._request("GET", f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
        except Exception:
            return []
        valid: List[str] = []
        for iface in (data or []):
            if iface.get("name") == "lo":
                continue
            for ip_obj in iface.get("ip-addresses", []):
                if ip_obj.get("ip-address-type") == "ipv4" and ip_obj.get("ip-address") != "127.0.0.1":
                    valid.append(ip_obj["ip-address"])
        return valid

    async def action_vm(self, node: str, vmid: str, action: str, body: Optional[Dict[str, Any]] = None) -> str:
        """Execute action on a VM (start, stop, reboot, shutdown, suspend, resume, clone).

        Returns the Proxmox UPID task ID.
        """
        if action == "clone":
            path = f"/nodes/{node}/qemu/{vmid}/clone"
        else:
            path = f"/nodes/{node}/qemu/{vmid}/status/{action}"
        data = await self._request("POST", path, json=body)
        return data or ""

    async def action_machine(self, node: str, vmid: str, pve_type: str, action: str, body: Optional[Dict[str, Any]] = None) -> str:
        """Execute start/stop/etc. on a VM (qemu) or LXC. Returns the UPID task ID."""
        kind = "qemu" if pve_type == "vm" else "lxc"
        data = await self._request("POST", f"/nodes/{node}/{kind}/{vmid}/status/{action}", json=body)
        return data or ""

    async def get_machine_status(self, node: str, vmid: str, pve_type: str) -> Optional[str]:
        """Fresh power status ('running'/'stopped'/...) of a VM or LXC."""
        kind = "qemu" if pve_type == "vm" else "lxc"
        data = await self._request("GET", f"/nodes/{node}/{kind}/{vmid}/status/current")
        return data.get("status") if isinstance(data, dict) else None

    async def set_machine_config(self, node: str, vmid: str, pve_type: str, params: Dict[str, Any]) -> None:
        """Change machine configuration (cores, memory, ...).

        Requires VM.Config.* privileges on the token. On running machines the
        change applies live only if hotplug allows it, otherwise it takes
        effect at the next start — Proxmox accepts the update either way.
        """
        kind = "qemu" if pve_type == "vm" else "lxc"
        await self._request("PUT", f"/nodes/{node}/{kind}/{vmid}/config", json=params)

    async def get_cluster_status(self) -> List[Dict[str, Any]]:
        try:
            data = await self._request("GET", "/cluster/status")
        except Exception as e:
            logger.exception("Error fetching cluster status: %s", e)
            return []
        members = data.get("members", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        out = []
        for m in members:
            if not isinstance(m, dict):
                continue
            # /cluster/status returns a flat list; skip the cluster summary
            # entry (no "online" key) and keep actual node entries.
            if m.get("type") and m.get("type") != "node":
                continue
            if "online" not in m:
                continue
            out.append({
                "id": f"node-{m.get('name')}",
                "name": m.get("name"),
                "type": "node",
                "status": "online" if m.get("online", 0) == 1 else "offline",
            })
        return out

    async def close(self):
        await self._client.aclose()


async def create_client_if_possible(base_url: Optional[str], token: Optional[str], secret: Optional[str], insecure: bool = False) -> Optional[ProxmoxClient]:
    # If base_url not provided, attempt to read from DB settings
    if not base_url:
        try:
            settings = get_settings()
            base_url = settings.get("proxmox_url")
            if not token:
                token = settings.get("proxmox_token_id")
            if not secret:
                secret = settings.get("proxmox_secret")
            if not insecure:
                insecure = settings.get("proxmox_insecure", "false").lower() in ("1", "true", "yes")
        except Exception as e:
            logger.exception("Error reading proxmox settings from DB: %s", e)
            return None
    if not base_url:
        return None
    # token may be provided as tokenid:secret or separately; try to split
    token_id = None
    token_secret = None
    if token and "=" in token:
        # support PVEAPIToken style tokenid=secret
        parts = token.split("=", 1)
        token_id = parts[0]
        token_secret = parts[1]
    else:
        token_id = token
        token_secret = secret
    return ProxmoxClient(base_url, token_id, token_secret, insecure=insecure)

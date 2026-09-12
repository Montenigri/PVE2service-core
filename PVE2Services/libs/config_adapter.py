from .db_adapter import DBAdapter


class ConfigAdapter:
    def __init__(self, db: DBAdapter = None):
        self.db = db or DBAdapter()

    def save_settings(self, proxmox_url: str, token_id: str, secret: str, insecure: bool = False):
        # re-use settings table as key/value
        self.db.set_setting("proxmox_url", proxmox_url)
        self.db.set_setting("proxmox_token_id", token_id)
        self.db.set_setting("proxmox_secret", secret)
        self.db.set_setting("proxmox_insecure", str(insecure).lower())

    def get_settings(self) -> dict:
        s = {}
        s["proxmox_url"] = self.db.get_setting("proxmox_url")
        s["proxmox_token_id"] = self.db.get_setting("proxmox_token_id")
        s["proxmox_secret"] = self.db.get_setting("proxmox_secret")
        s["proxmox_insecure"] = self.db.get_setting("proxmox_insecure")
        return s

from dataclasses import dataclass


@dataclass
class UserRecord:
    id: int
    username: str
    password_hash: str

"""Verified credential deletion; unavailable storage is not proof of absence."""


class CredentialRemovalError(RuntimeError):
    pass


def delete_verified(service: str, account: str) -> None:
    import keyring

    try:
        if keyring.get_password(service, account) is None:
            return
        keyring.delete_password(service, account)
        remaining = keyring.get_password(service, account)
    except Exception:
        # Backend exceptions can include credential content; expose only our safe message.
        raise CredentialRemovalError("Credential removal could not be verified.") from None
    if remaining is not None:
        raise CredentialRemovalError("The credential is still stored.")

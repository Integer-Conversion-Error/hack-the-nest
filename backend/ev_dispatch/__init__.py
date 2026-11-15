"""EV fleet dispatch solver for the approved Van 2 charge range."""

VAN2_CHARGE_PCTS = range(20, 36)
DEFAULT_VAN2_CHARGE_PCT = VAN2_CHARGE_PCTS[-1]


def validate_van2_charge_pct(charge: int) -> None:
    if type(charge) is not int or charge not in VAN2_CHARGE_PCTS:
        raise ValueError(
            f"Van 2 charge must be a whole percentage from {VAN2_CHARGE_PCTS.start} "
            f"to {VAN2_CHARGE_PCTS[-1]}, got {charge!r}."
        )

def uninstall_hook(env):
    """Remove the labour code limits stored as system parameters."""
    env["ir.config_parameter"].sudo().search(
        [("key", "=like", "hr_shift_planning.%")]
    ).unlink()

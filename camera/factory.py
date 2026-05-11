from core.global_config import CONFIG


def create_camera_provider():
    profile = CONFIG.get("profile", "pc")
    if profile == "pi":
        from camera.pi_provider import PiProvider
        return PiProvider()
    else:
        from camera.pc_provider import PCProvider
        return PCProvider()

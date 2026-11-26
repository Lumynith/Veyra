import sys


def main() -> int:
    if sys.argv[1:] == ["--script-host"]:  # release build: the script sandbox process (see automation/sandbox.py)
        from automation import script_host
        script_host.main()
        return 0
    from core.startup import create_qt, user_settings
    create_qt(sys.argv)
    settings = user_settings()
    splash = None
    if settings.get("ui.animations", True):
        from ui import theme
        from ui.splash import Splash
        theme.set_palette(settings.get("ui.theme", "dark"), settings.get("ui.accent", "default"))
        splash = Splash()
        splash.show()
        splash.play_intro()
        splash.step("Loading modules", 0.05)
    from core.application import Application  # the heavy imports (OpenCV, the engine, all pages)
    app = Application(sys.argv, progress=splash.step if splash else None)
    if splash:
        splash.finish(app.main_window)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())

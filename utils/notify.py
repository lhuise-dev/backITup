def send_notification(title: str, message: str):
    try:
        from plyer import notification

        notification.notify(
            title=title,
            message=message,
            app_name="backITup",
            timeout=5,
        )
    except Exception:
        pass

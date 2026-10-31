from celery import shared_task

from social import push


@shared_task(name="Send push notification")
def send_push(user_id, body, route):
    """Send a push to each of a user's phones."""
    return push.send(user_id, body, route)

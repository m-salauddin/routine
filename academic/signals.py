# academic/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import TemporarySwapRequest, Notification

@receiver(post_save, sender=TemporarySwapRequest)
def create_swap_notification(sender, instance, created, **kwargs):
    # 1. when a new request is created (PENDING)
    if created:
        Notification.objects.create(
            recipient=instance.target_teacher,
            sender=instance.requester,
            notification_type='SWAP_REQ',
            title='New Class Swap Request',
            message=f"{instance.requester.username} has requested a {instance.get_swap_type_display()} with you for {instance.swap_date}.",
            action_url=f"/teacher/swap-requests"  # Frontend route
        )
    # 2.when the status of the request changes (ACCEPTED or REJECTED)
    else:
        if instance.status == 'ACCEPTED':
            Notification.objects.create(
                recipient=instance.requester,
                sender=instance.target_teacher,
                notification_type='SWAP_ACC',
                title='Swap Request Accepted',
                message=f"{instance.target_teacher.username} has ACCEPTED your class swap request for {instance.swap_date}.",
                action_url="/teacher/routine"
            )
        elif instance.status == 'REJECTED':
            Notification.objects.create(
                recipient=instance.requester,
                sender=instance.target_teacher,
                notification_type='SWAP_REJ',
                title='Swap Request Rejected',
                message=f"{instance.target_teacher.username} has REJECTED your class swap request for {instance.swap_date}.",
                action_url="/teacher/routine"
            )
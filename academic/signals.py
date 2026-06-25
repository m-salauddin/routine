# academic/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import TemporarySwapRequest, Notification, ActivityLog

@receiver(post_save, sender=TemporarySwapRequest)
def handle_swap_events(sender, instance, created, **kwargs):
    # 1. when a new request is created (Notification + Log)
    if created:
        # Notification
        Notification.objects.create(
            recipient=instance.target_teacher,
            sender=instance.requester,
            notification_type='SWAP_REQ',
            title='New Class Swap Request',
            message=f"{instance.requester.username} has requested a {instance.get_swap_type_display()} for {instance.swap_date}.",
            action_url="/teacher/swap-requests"
        )
        # Activity Log
        log = ActivityLog.objects.create(
            actor=instance.requester,
            action_description=f"Sent a {instance.get_swap_type_display()} request to {instance.target_teacher.username} for {instance.swap_date}.",
            severity='INFO'
        )
        log.related_users.add(instance.target_teacher) 

    
    else:
        if instance.status == 'ACCEPTED':
            # Notification
            Notification.objects.create(
                recipient=instance.requester,
                sender=instance.target_teacher,
                notification_type='SWAP_ACC',
                title='Swap Request Accepted',
                message=f"{instance.target_teacher.username} has ACCEPTED your swap request for {instance.swap_date}.",
                action_url="/teacher/routine"
            )
            # Activity Log
            log = ActivityLog.objects.create(
                actor=instance.target_teacher,
                action_description=f"ACCEPTED a class swap request from {instance.requester.username} for {instance.swap_date}.",
                severity='SUCCESS'
            )
            log.related_users.add(instance.requester)

        elif instance.status == 'REJECTED':
            # Notification
            Notification.objects.create(
                recipient=instance.requester,
                sender=instance.target_teacher,
                notification_type='SWAP_REJ',
                title='Swap Request Rejected',
                message=f"{instance.target_teacher.username} has REJECTED your swap request for {instance.swap_date}.",
                action_url="/teacher/routine"
            )
            # Activity Log
            log = ActivityLog.objects.create(
                actor=instance.target_teacher,
                action_description=f"REJECTED a class swap request from {instance.requester.username} for {instance.swap_date}.",
                severity='WARNING'
            )
            log.related_users.add(instance.requester)
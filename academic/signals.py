# academic/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import TemporarySwapRequest, ActivityLog
# Notification ইমপোর্ট করার দরকার নেই, কারণ এটি এখন views.py থেকে যাচ্ছে

@receiver(post_save, sender=TemporarySwapRequest)
def handle_swap_events(sender, instance, created, **kwargs):
    # 1. When a new request is created (Only Activity Log)
    if created:
        log = ActivityLog.objects.create(
            actor=instance.requester,
            action_description=f"Sent a {instance.get_swap_type_display()} request to {instance.target_teacher.username} for {instance.swap_date}.",
            severity='INFO'
        )
        log.related_users.add(instance.target_teacher) 

    # 2. When a request is updated -> ACCEPTED or REJECTED (Only Activity Log)
    else:
        if instance.status == 'ACCEPTED':
            log = ActivityLog.objects.create(
                actor=instance.target_teacher,
                action_description=f"ACCEPTED a class swap request from {instance.requester.username} for {instance.swap_date}.",
                severity='SUCCESS'
            )
            log.related_users.add(instance.requester)

        elif instance.status == 'REJECTED':
            log = ActivityLog.objects.create(
                actor=instance.target_teacher,
                action_description=f"REJECTED a class swap request from {instance.requester.username} for {instance.swap_date}.",
                severity='WARNING'
            )
            log.related_users.add(instance.requester)
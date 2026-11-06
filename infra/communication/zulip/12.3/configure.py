"""Run with Zulip 12.3 manage.py shell; parameters come from the environment."""

import json
import os

from zerver.actions.realm_settings import (
    do_set_realm_property,
    do_set_realm_user_default_setting,
)
from zerver.actions.streams import bulk_add_subscriptions
from zerver.actions.user_settings import do_change_user_setting
from zerver.models import Realm, RealmUserDefault, Stream, Subscription, UserProfile


def ids(name):
    values = os.environ.get(name, "").split(",")
    return list(dict.fromkeys(int(value) for value in values if value.strip()))


realm = Realm.objects.get(id=int(os.environ["REALM_ID"]))
actor = UserProfile.objects.get(realm=realm, id=int(os.environ["ACTOR_ID"]))
action = os.environ["ACTION"]
result = {"realm_id": realm.id, "actor_id": actor.id, "changed": 0}

if action == "ui":
    defaults = RealmUserDefault.objects.get(realm=realm)
    settings = [
        (realm, "default_language", "zh-hans", do_set_realm_property),
        (defaults, "color_scheme", 3, do_set_realm_user_default_setting),
    ]
    human_ids = ids("HUMAN_IDS")
    for user_id in human_ids:
        user = UserProfile.objects.get(realm=realm, id=user_id)
        settings.extend([
            (user, "default_language", "zh-hans", do_change_user_setting),
            (user, "color_scheme", 3, do_change_user_setting),
        ])
    for obj, name, value, change in settings:
        if getattr(obj, name) != value:
            change(obj, name, value, acting_user=actor)
            result["changed"] += 1
    result.update(human_ids=human_ids, default_language="zh-hans", color_scheme=3)
elif action == "subscribe":
    bots = [UserProfile.objects.get(realm=realm, id=value) for value in ids("BOT_IDS")]
    channels = [Stream.objects.get(realm=realm, id=value) for value in ids("CHANNEL_IDS")]
    result["already_subscribed"] = 0
    for channel in channels:
        active = set(Subscription.objects.filter(
            recipient_id=channel.recipient_id, user_profile__in=bots, active=True,
        ).values_list("user_profile_id", flat=True))
        result["already_subscribed"] += len(active)
        missing = [bot for bot in bots if bot.id not in active]
        if missing:
            changed, _ = bulk_add_subscriptions(realm, [channel], missing, acting_user=actor)
            result["changed"] += len(changed)
    result.update(
        bot_ids=[bot.id for bot in bots], channel_ids=[channel.id for channel in channels]
    )
else:
    raise ValueError(action)

print(json.dumps(result))

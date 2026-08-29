"""French push notification copy (informal, gender-neutral)."""

NOTIFICATION_MESSAGES_FR = {
    "male": {
        "meal_reminder": {
            "breakfast": {
                "body": "Bonjour ! Prends un petit-déjeuner ou un café\nQuand tu peux — note-le 🌅",
            },
            "lunch": {
                "body_template": "C'est l'heure du déjeuner ! Il te reste {remaining} cal\nQu'est-ce qu'il y a dans l'assiette ? 🥗",
            },
            "dinner": {
                "body_template": "C'est l'heure du dîner ! Il te reste {remaining} cal\nFais-en sorte ce soir 🌙",
            },
        },
        "daily_summary": {
            "zero_logs": {
                "body": "Journée chargée ? Pas de stress\nNote un repas rapide quand tu peux 📝",
            },
            "on_target": {
                "body_template": "Bravo ! {percentage}% de ton objectif\nGarde ce rythme 🎉",
            },
            "under_goal": {
                "body_template": "Presque ! Il te reste {deficit} cal\nUn encas malin peut clôturer 💪",
            },
            "slightly_over": {
                "body_template": "Pas de stress, {excess} cal de trop\nContinue et reste régulier 😎",
            },
            "way_over": {
                "body_template": "Tout va bien, {excess} cal de trop\nDemain est un nouveau départ 🤙",
            },
        },
        "trial_expiry": {
            "2d": {
                "title": "Nutree",
                "body": "Ton essai se termine dans 2 jours\nGarde ta série ⏳",
            },
            "1d": {
                "title": "Nutree",
                "body": "Ton essai gratuit se termine bientôt\nContinue ta progression 🔥",
            },
        },
        "hydration_reminder": {
            "afternoon": {
                "body_template": "Mi-journée ! {consumed_ml}ml bus, encore {remaining_ml}ml\nReste hydraté 💧",
            },
            "evening": {
                "body_template": "Presque ! {consumed_ml}ml enregistrés aujourd'hui\nPlus que {remaining_ml}ml pour ton objectif 💧",
            },
        },
        "subscription_hook": {
            "title": "Ton plan Nutree est prêt",
            "body": "Ton plan nutrition personnalisé est prêt\nAbonne-toi pour débloquer la suite ✨",
        },
    },
    "female": {
        "meal_reminder": {
            "breakfast": {
                "body": "Bonjour ! Prends un petit-déjeuner ou un café\nQuand tu peux — note-le 🌅",
            },
            "lunch": {
                "body_template": "C'est l'heure du déjeuner ! Il te reste {remaining} cal\nQu'est-ce qu'il y a dans l'assiette ? 🥗",
            },
            "dinner": {
                "body_template": "C'est l'heure du dîner ! Il te reste {remaining} cal\nFais-en sorte ce soir 🌙",
            },
        },
        "daily_summary": {
            "zero_logs": {
                "body": "Journée chargée ? Pas de stress\nNote un repas rapide quand tu peux 📝",
            },
            "on_target": {
                "body_template": "Bravo ! {percentage}% de ton objectif\nGarde ce rythme 🎉",
            },
            "under_goal": {
                "body_template": "Presque ! Il te reste {deficit} cal\nUn encas malin peut clôturer 💪",
            },
            "slightly_over": {
                "body_template": "Pas de stress, {excess} cal de trop\nContinue et reste régulier 😎",
            },
            "way_over": {
                "body_template": "Tout va bien, {excess} cal de trop\nDemain est un nouveau départ 🤙",
            },
        },
        "trial_expiry": {
            "2d": {
                "title": "Nutree",
                "body": "Ton essai se termine dans 2 jours\nGarde ta série ⏳",
            },
            "1d": {
                "title": "Nutree",
                "body": "Ton essai gratuit se termine bientôt\nContinue ta progression 🔥",
            },
        },
        "hydration_reminder": {
            "afternoon": {
                "body_template": "Mi-journée ! {consumed_ml}ml bus, encore {remaining_ml}ml\nReste hydraté 💧",
            },
            "evening": {
                "body_template": "Presque ! {consumed_ml}ml enregistrés aujourd'hui\nPlus que {remaining_ml}ml pour ton objectif 💧",
            },
        },
        "subscription_hook": {
            "title": "Ton plan Nutree est prêt",
            "body": "Ton plan nutrition personnalisé est prêt\nAbonne-toi pour débloquer la suite ✨",
        },
    },
}

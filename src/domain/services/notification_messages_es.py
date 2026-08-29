"""Spanish push notification copy (informal, gender-neutral)."""

NOTIFICATION_MESSAGES_ES = {
    "male": {
        "meal_reminder": {
            "breakfast": {
                "body": "¡Buenos días! Desayuna o toma un café\nCuando puedas, regístralo 🌅",
            },
            "lunch": {
                "body_template": "¡Es hora del almuerzo! Te quedan {remaining} cal\n¿Qué hay en el plato? 🥗",
            },
            "dinner": {
                "body_template": "¡Hora de cenar! Te quedan {remaining} cal\nHaz que cuente esta noche 🌙",
            },
        },
        "daily_summary": {
            "zero_logs": {
                "body": "¿Día ajetreado? Sin estrés\nRegistra una comida rápida cuando puedas 📝",
            },
            "on_target": {
                "body_template": "¡Lo lograste! {percentage}% de tu meta\nSigue con ese ritmo 🎉",
            },
            "under_goal": {
                "body_template": "¡Casi! Te faltan {deficit} cal\nUn snack inteligente puede cerrarlo 💪",
            },
            "slightly_over": {
                "body_template": "Sin estrés, {excess} cal de más\nSigue adelante y mantén la constancia 😎",
            },
            "way_over": {
                "body_template": "Todo bien, {excess} cal de más\nMañana es un nuevo día 🤙",
            },
        },
        "trial_expiry": {
            "2d": {
                "title": "Nutree",
                "body": "Tu prueba termina en 2 días\nMantén tu racha ⏳",
            },
            "1d": {
                "title": "Nutree",
                "body": "Tu prueba gratuita termina pronto\nSigue con tu progreso 🔥",
            },
        },
        "hydration_reminder": {
            "afternoon": {
                "body_template": "¡A mitad del día! {consumed_ml}ml tomados, faltan {remaining_ml}ml\nMantente hidratado 💧",
            },
            "evening": {
                "body_template": "¡Casi! {consumed_ml}ml registrados hoy\nSolo {remaining_ml}ml para tu meta 💧",
            },
        },
        "subscription_hook": {
            "title": "Tu plan Nutree está listo",
            "body": "Tu plan de nutrición personalizado está listo\nSuscríbete para desbloquear el siguiente paso ✨",
        },
    },
    "female": {
        "meal_reminder": {
            "breakfast": {
                "body": "¡Buenos días! Desayuna o toma un café\nCuando puedas, regístralo 🌅",
            },
            "lunch": {
                "body_template": "¡Es hora del almuerzo! Te quedan {remaining} cal\n¿Qué hay en el plato? 🥗",
            },
            "dinner": {
                "body_template": "¡Hora de cenar! Te quedan {remaining} cal\nHaz que cuente esta noche 🌙",
            },
        },
        "daily_summary": {
            "zero_logs": {
                "body": "¿Día ajetreado? Sin estrés\nRegistra una comida rápida cuando puedas 📝",
            },
            "on_target": {
                "body_template": "¡Lo lograste! {percentage}% de tu meta\nSigue con ese ritmo 🎉",
            },
            "under_goal": {
                "body_template": "¡Casi! Te faltan {deficit} cal\nUn snack inteligente puede cerrarlo 💪",
            },
            "slightly_over": {
                "body_template": "Sin estrés, {excess} cal de más\nSigue adelante y mantén la constancia 😎",
            },
            "way_over": {
                "body_template": "Todo bien, {excess} cal de más\nMañana es un nuevo día 🤙",
            },
        },
        "trial_expiry": {
            "2d": {
                "title": "Nutree",
                "body": "Tu prueba termina en 2 días\nMantén tu racha ⏳",
            },
            "1d": {
                "title": "Nutree",
                "body": "Tu prueba gratuita termina pronto\nSigue con tu progreso 🔥",
            },
        },
        "hydration_reminder": {
            "afternoon": {
                "body_template": "¡A mitad del día! {consumed_ml}ml tomados, faltan {remaining_ml}ml\nMantente hidratado 💧",
            },
            "evening": {
                "body_template": "¡Casi! {consumed_ml}ml registrados hoy\nSolo {remaining_ml}ml para tu meta 💧",
            },
        },
        "subscription_hook": {
            "title": "Tu plan Nutree está listo",
            "body": "Tu plan de nutrición personalizado está listo\nSuscríbete para desbloquear el siguiente paso ✨",
        },
    },
}

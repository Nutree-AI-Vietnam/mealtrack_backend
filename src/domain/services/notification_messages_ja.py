"""Japanese push notification copy (informal, gender-neutral)."""

NOTIFICATION_MESSAGES_JA = {
    "male": {
        "meal_reminder": {
            "breakfast": {
                "body": "おはよう！軽食かコーヒーをどうぞ\n時間があれば記録してね 🌅",
            },
            "lunch": {
                "body_template": "ランチの時間！残り{remaining} cal\n今日のランチは何？ 🥗",
            },
            "dinner": {
                "body_template": "ディナーの時間！残り{remaining} cal\n今夜も頑張ろう 🌙",
            },
        },
        "daily_summary": {
            "zero_logs": {
                "body": "忙しい一日？大丈夫\n時間があれば1食記録してね 📝",
            },
            "on_target": {
                "body_template": "ナイス！目標の{percentage}%\nこの調子でいこう 🎉",
            },
            "under_goal": {
                "body_template": "あと少し！残り{deficit} cal\n賢いスナックで届けよう 💪",
            },
            "slightly_over": {
                "body_template": "大丈夫、{excess} calオーバー\n続けてコツコツいこう 😎",
            },
            "way_over": {
                "body_template": "問題なし、{excess} calオーバー\n明日は新しいスタート 🤙",
            },
        },
        "trial_expiry": {
            "2d": {
                "title": "Nutree",
                "body": "トライアルはあと2日で終了\nストリークをキープしよう ⏳",
            },
            "1d": {
                "title": "Nutree",
                "body": "無料トライアルがもうすぐ終了\nこの調子で続けよう 🔥",
            },
        },
        "hydration_reminder": {
            "afternoon": {
                "body_template": "半分まで来た！{consumed_ml}ml飲んだ、あと{remaining_ml}ml\n水分補給を忘れずに 💧",
            },
            "evening": {
                "body_template": "もう少し！今日は{consumed_ml}ml記録済み\nあと{remaining_ml}mlで目標達成 💧",
            },
        },
        "subscription_hook": {
            "title": "Nutreeプランの準備ができました",
            "body": "パーソナライズされた栄養プランが準備完了\n登録して次のステップを解放 ✨",
        },
    },
    "female": {
        "meal_reminder": {
            "breakfast": {
                "body": "おはよう！軽食かコーヒーをどうぞ\n時間があれば記録してね 🌅",
            },
            "lunch": {
                "body_template": "ランチの時間！残り{remaining} cal\n今日のランチは何？ 🥗",
            },
            "dinner": {
                "body_template": "ディナーの時間！残り{remaining} cal\n今夜も頑張ろう 🌙",
            },
        },
        "daily_summary": {
            "zero_logs": {
                "body": "忙しい一日？大丈夫\n時間があれば1食記録してね 📝",
            },
            "on_target": {
                "body_template": "ナイス！目標の{percentage}%\nこの調子でいこう 🎉",
            },
            "under_goal": {
                "body_template": "あと少し！残り{deficit} cal\n賢いスナックで届けよう 💪",
            },
            "slightly_over": {
                "body_template": "大丈夫、{excess} calオーバー\n続けてコツコツいこう 😎",
            },
            "way_over": {
                "body_template": "問題なし、{excess} calオーバー\n明日は新しいスタート 🤙",
            },
        },
        "trial_expiry": {
            "2d": {
                "title": "Nutree",
                "body": "トライアルはあと2日で終了\nストリークをキープしよう ⏳",
            },
            "1d": {
                "title": "Nutree",
                "body": "無料トライアルがもうすぐ終了\nこの調子で続けよう 🔥",
            },
        },
        "hydration_reminder": {
            "afternoon": {
                "body_template": "半分まで来た！{consumed_ml}ml飲んだ、あと{remaining_ml}ml\n水分補給を忘れずに 💧",
            },
            "evening": {
                "body_template": "もう少し！今日は{consumed_ml}ml記録済み\nあと{remaining_ml}mlで目標達成 💧",
            },
        },
        "subscription_hook": {
            "title": "Nutreeプランの準備ができました",
            "body": "パーソナライズされた栄養プランが準備完了\n登録して次のステップを解放 ✨",
        },
    },
}

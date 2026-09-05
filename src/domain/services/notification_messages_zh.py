"""Chinese (Simplified) push notification copy (informal, gender-neutral)."""

NOTIFICATION_MESSAGES_ZH = {
    "male": {
        "meal_reminder": {
            "breakfast": {
                "body": "早上好！吃点东西或喝杯咖啡\n有空记得记录一下 🌅",
            },
            "lunch": {
                "body_template": "午餐时间！还剩{remaining} cal\n今天吃什么？ 🥗",
            },
            "dinner": {
                "body_template": "晚餐时间！还剩{remaining} cal\n今晚也要加油哦 🌙",
            },
        },
        "daily_summary": {
            "zero_logs": {
                "body": "今天很忙？没关系\n有空快速记录一餐 📝",
            },
            "on_target": {
                "body_template": "太棒了！完成目标的{percentage}%\n继续保持 🎉",
            },
            "under_goal": {
                "body_template": "快完成了！还差{deficit} cal\n来个聪明的小零食补一下 💪",
            },
            "slightly_over": {
                "body_template": "别紧张，超出{excess} cal\n继续坚持下去 😎",
            },
            "way_over": {
                "body_template": "没关系，超出{excess} cal\n明天重新开始 🤙",
            },
        },
        "trial_expiry": {
            "2d": {
                "title": "Nutree",
                "body": "试用还剩2天\n继续保持你的连续记录 ⏳",
            },
            "1d": {
                "title": "Nutree",
                "body": "免费试用即将结束\n别让你的进度中断 🔥",
            },
        },
        "hydration_reminder": {
            "afternoon": {
                "body_template": "半天过去了！已喝{consumed_ml}ml，还差{remaining_ml}ml\n记得补水 💧",
            },
            "evening": {
                "body_template": "快完成了！今天已记录{consumed_ml}ml\n还差{remaining_ml}ml就达标 💧",
            },
        },
        "subscription_hook": {
            "title": "你的 Nutree 计划已就绪",
            "body": "你的个性化营养计划已准备好\n订阅以解锁下一步 ✨",
        },
    },
    "female": {
        "meal_reminder": {
            "breakfast": {
                "body": "早上好！吃点东西或喝杯咖啡\n有空记得记录一下 🌅",
            },
            "lunch": {
                "body_template": "午餐时间！还剩{remaining} cal\n今天吃什么？ 🥗",
            },
            "dinner": {
                "body_template": "晚餐时间！还剩{remaining} cal\n今晚也要加油哦 🌙",
            },
        },
        "daily_summary": {
            "zero_logs": {
                "body": "今天很忙？没关系\n有空快速记录一餐 📝",
            },
            "on_target": {
                "body_template": "太棒了！完成目标的{percentage}%\n继续保持 🎉",
            },
            "under_goal": {
                "body_template": "快完成了！还差{deficit} cal\n来个聪明的小零食补一下 💪",
            },
            "slightly_over": {
                "body_template": "别紧张，超出{excess} cal\n继续坚持下去 😎",
            },
            "way_over": {
                "body_template": "没关系，超出{excess} cal\n明天重新开始 🤙",
            },
        },
        "trial_expiry": {
            "2d": {
                "title": "Nutree",
                "body": "试用还剩2天\n继续保持你的连续记录 ⏳",
            },
            "1d": {
                "title": "Nutree",
                "body": "免费试用即将结束\n别让你的进度中断 🔥",
            },
        },
        "hydration_reminder": {
            "afternoon": {
                "body_template": "半天过去了！已喝{consumed_ml}ml，还差{remaining_ml}ml\n记得补水 💧",
            },
            "evening": {
                "body_template": "快完成了！今天已记录{consumed_ml}ml\n还差{remaining_ml}ml就达标 💧",
            },
        },
        "subscription_hook": {
            "title": "你的 Nutree 计划已就绪",
            "body": "你的个性化营养计划已准备好\n订阅以解锁下一步 ✨",
        },
    },
}

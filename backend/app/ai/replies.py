from app.ai.scope import normalize

# Order: Darija Latin, Arabic-script Darija, French, English, mixed Darija/French.
STYLES = ("darija_latin", "darija_arabic", "french", "english", "mixed")
TEXT = {
    "redirect": (
        "Ana hna bach n3awnk f les produits, commandes w services dyalna 😊 Chno n9der n3awnk fih?",
        "أنا هنا باش نعاونك فالمنتوجات والطلبات والخدمات ديالنا 😊 شنو بغيتي تعرف؟",
        "Je peux surtout t'aider avec nos produits, commandes et services 😊",
        "I can help with our products, orders and services 😊",
        "Ana hna bach n3awnk f les produits, commandes w services dyalna 😊"),
    "clarify": (
        "Chno bghiti t9elleb 3lih? Golli chno kay3jbk w ch7al l budget dyalk 😊",
        "شنو بغيتي تقلب عليه؟ قول ليا شنو كيعجبك وشحال الميزانية ديالك 😊",
        "Tu cherches quoi ? Dis-moi tes préférences et ton budget 😊",
        "What are you shopping for? Tell me your preferences and budget 😊",
        "Chno bghiti? Golli tes préférences w ton budget 😊"),
    "shorten": (
        "Lmessage twil chwiya. T9der tsift lia sou2al 9sir?",
        "الميساج طويل شوية. تقدر تصيفط ليا سؤال قصير؟",
        "Ton message est un peu long. Tu peux le raccourcir ?",
        "Your message is a little long. Could you shorten it?",
        "Lmessage twil chwiya, tu peux le raccourcir ?"),
    "notice": (
        "Chwiya 3afak 😊 3awed jreb mn b3d chwiya.",
        "بشوية عافاك 😊 عاود جرب من بعد شوية.",
        "Un petit moment, s'il te plaît 😊 Réessaie un peu plus tard.",
        "One moment, please 😊 Try again a little later.",
        "Chwiya 3afak 😊 Réessaie un peu plus tard."),
    "hello": ("Salam! Kifach n3awnk? 😊", "سلام! كيفاش نعاونك؟ 😊", "Bonjour ! Comment je peux t'aider ? 😊",
              "Hello! How can I help? 😊", "Salam! Comment je peux t'aider ? 😊"),
    "thanks": ("Bla jmil 😊", "بلا جميل 😊", "Avec plaisir 😊", "You're welcome 😊", "Bla jmil, avec plaisir 😊"),
    "bye": ("Bslama 😊", "بسلامة 😊", "À bientôt 😊", "Goodbye 😊", "Bslama, à bientôt 😊"),
    "well": ("Labas, chokran! W nta? 😊", "لاباس، شكرا! ونتا؟ 😊", "Ça va, merci 😊", "Doing well, thanks 😊", "Labas, merci 😊"),
}


def local_reply(kind, style, text=""):
    if kind == "social":
        value = normalize(text)
        if value.strip(" .!?") in ("ah ok", "ah okay", "ok", "okay", "safi fhemtk"):
            return "👍"
        kind = "thanks" if any(w in value for w in ("merci", "chokr", "choukr", "thank", "شكرا")) else (
            "bye" if any(w in value for w in ("revoir", "bye", "bslama", "بسلامة", "bonne journée")) else "well" if value.startswith(("labas", "لاباس")) else "hello")
    return TEXT[kind][STYLES.index(style)]

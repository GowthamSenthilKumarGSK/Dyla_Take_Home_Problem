"""Research questions for the 8-question evaluation runner."""

QUESTIONS: list[dict] = [
    {
        "id": 1,
        "question": (
            "Who is the current Managing Director of Titan Company, "
            "and when did they take over?"
        ),
        "introduces": ["Titan Company", "Ajoy Chawla"],
        "reuses": [],
        "difficulty": "easy",
    },
    {
        "id": 2,
        "question": (
            "What was Titan Company's total revenue in FY 2024-25, "
            "and how did it compare to the previous year?"
        ),
        "introduces": [],
        "reuses": ["Titan Company"],
        "difficulty": "easy-medium",
    },
    {
        "id": 3,
        "question": (
            "Who founded Infosys, and what is the company's current "
            "market capitalization?"
        ),
        "introduces": ["Infosys", "N.R. Narayana Murthy"],
        "reuses": [],
        "difficulty": "medium",
    },
    {
        "id": 4,
        "question": (
            "What are the main business divisions of Titan Company, "
            "and who leads each one?"
        ),
        "introduces": [],
        "reuses": ["Titan Company", "Ajoy Chawla"],
        "difficulty": "medium",
    },
    {
        "id": 5,
        "question": (
            "How does Infosys's revenue compare to TCS's revenue "
            "in FY 2025-26?"
        ),
        "introduces": ["TCS"],
        "reuses": ["Infosys"],
        "difficulty": "medium-hard",
    },
    {
        "id": 6,
        "question": (
            "What was the Chandrayaan-3 mission's landing date, "
            "and which ISRO scientist led the mission?"
        ),
        "introduces": ["Chandrayaan-3", "ISRO"],
        "reuses": [],
        "difficulty": "medium-hard",
    },
    {
        "id": 7,
        "question": (
            "What is the latest available share price of Titan Company, "
            "and how does it compare to the closing price on the last "
            "trading day before Ajoy Chawla became Managing Director? "
            "Include the percentage change."
        ),
        "introduces": [],
        "reuses": ["Titan Company", "Ajoy Chawla"],
        "difficulty": "hard",
    },
    {
        "id": 8,
        "question": (
            "Which country has completed more successful Moon landings: "
            "India or China? List each country's successful lunar landing "
            "missions and their dates."
        ),
        "introduces": ["CNSA", "China lunar program"],
        "reuses": ["ISRO", "Chandrayaan-3"],
        "difficulty": "hard",
    },
]

"""Tests for event_relevance.classify_relevance() - the source-agnostic
evidence-scoring relevance model. Pure logic, no I/O. Stdlib unittest only.

Covers the task's required matrix: CS/software, cybersecurity, AI/ML, data,
cloud, networking, DevOps, robotics, engineering, hackathons, tech careers,
startups, entrepreneurship, VC, innovation, product, generic business,
ambiguous keywords, unrelated events, and weak-signal combinations.

Run with: python -m unittest test_event_relevance -v
"""
import unittest

from event_relevance import classify_relevance


def _event(name="", description=None, categories=None, audiences=None):
    return {"name": name, "description": description, "categories": categories, "audiences": audiences}


class AdversarialFalsePositiveTests(unittest.TestCase):
    """Adversarial audit: generic/ambiguous titles that must NOT auto-include
    just because weak signals or an event-format word pushed the raw score
    to the threshold. Found by direct probing before the has_strong_signal
    gate was added - every one of these auto-included under the prior model.
    Must all land at REVIEW (none of them carries a strong tech/business
    phrase, and none carries a strong-exclude signal either)."""

    ADVERSARIAL_REVIEW_TITLES = (
        "Business Innovation Networking Conference",
        "Arizona Business Networking Summit",
        "Leadership and Innovation Conference",
        "Engineering Leadership Summit",
        "Product Innovation Networking Event",
        "Investor Networking Conference",
        "Venture Capital Networking Reception",
        "Campus Security Job Fair",
        "Physical Security Conference",
        "Healthcare Innovation Summit",
        "Community Business Expo",
        "Women in Business Networking",
        "Small Business Workshop",
        "Professional Networking Mixer",
        "Data Governance for Healthcare Administrators",
    )

    def test_adversarial_titles_stay_review(self):
        for title in self.ADVERSARIAL_REVIEW_TITLES:
            with self.subTest(title=title):
                result = classify_relevance(_event(title))
                self.assertEqual(result["decision"], "review", f"{title!r} -> {result}")

    def test_k12_robotics_competition_is_review_not_include(self):
        # Genuine strong tech signal ("robotics") conflicts with the K-12-only
        # exclude signal - conservative review, not a silent override either way.
        result = classify_relevance(_event("K-12 Robotics Competition"))
        self.assertEqual(result["decision"], "review")

    def test_cloud_themed_art_exhibition_is_review_not_include(self):
        # Weak "cloud" conflicts with the "art exhibition" exclude phrase -
        # review, and specifically never include.
        result = classify_relevance(_event("Cloud-themed art exhibition"))
        self.assertEqual(result["decision"], "review")

    def test_healthcare_ai_conference_includes_on_genuine_strong_signal(self):
        # NOT a false positive: "AI" is an unambiguous strong tech phrase
        # (validated word-boundary-safe elsewhere in this suite), so an AI
        # conference is genuinely relevant regardless of the "Healthcare"
        # domain qualifier - a tech-interested user plausibly wants to know
        # about AI conferences even when framed for a healthcare audience.
        result = classify_relevance(_event("Healthcare AI Conference"))
        self.assertEqual(result["decision"], "include")
        self.assertIn("ai", result["matched_topics"])

    def test_nonprofit_technology_workshop_includes_on_genuine_strong_signal(self):
        # NOT a false positive: "technology" is the taxonomy's own top-level
        # category, kept as an unambiguous strong phrase; "nonprofit" as a
        # host descriptor doesn't make a technology workshop irrelevant.
        result = classify_relevance(_event("Nonprofit Technology Workshop"))
        self.assertEqual(result["decision"], "include")

    def test_arts_and_technology_festival_includes_on_genuine_strong_signal(self):
        # Same reasoning as above - documented as a known edge case in the
        # report (an arts/tech fusion festival is genuinely ambiguous in
        # spirit, but "technology" is real evidence, not a buzzword chain).
        result = classify_relevance(_event("Arts and Technology Festival"))
        self.assertEqual(result["decision"], "include")


class HighConfidenceIncludeTests(unittest.TestCase):
    """The task's explicit high-confidence INCLUDE examples."""

    def test_cybersecurity_workshop(self):
        self.assertEqual(classify_relevance(_event("Cybersecurity Workshop"))["decision"], "include")

    def test_ai_ml_engineering_meetup(self):
        self.assertEqual(classify_relevance(_event("AI/ML Engineering Meetup"))["decision"], "include")

    def test_software_engineering_conference(self):
        self.assertEqual(classify_relevance(_event("Software Engineering Conference"))["decision"], "include")

    def test_cloud_devops_workshop(self):
        self.assertEqual(classify_relevance(_event("Cloud/DevOps Workshop"))["decision"], "include")

    def test_developer_meetup(self):
        self.assertEqual(classify_relevance(_event("Developer Meetup"))["decision"], "include")

    def test_hackathon(self):
        self.assertEqual(classify_relevance(_event("Hackathon"))["decision"], "include")

    def test_startup_founder_networking_event(self):
        self.assertEqual(classify_relevance(_event("Startup Founder Networking Event"))["decision"], "include")

    def test_technology_career_fair(self):
        self.assertEqual(classify_relevance(_event("Technology Career Fair"))["decision"], "include")

    def test_entrepreneurship_pitch_demo_event(self):
        self.assertEqual(classify_relevance(_event("Entrepreneurship Pitch/Demo Event"))["decision"], "include")

    def test_robotics_embedded_engineering_event(self):
        self.assertEqual(classify_relevance(_event("Robotics/Embedded Engineering Event"))["decision"], "include")


class AdversarialAuditRegressionGuardTests(unittest.TestCase):
    """The task's explicit "must remain include" list from the adversarial
    audit - guards against the has_strong_signal gate becoming too strict."""

    MUST_INCLUDE_TITLES = (
        "AI engineering meetup",
        "cybersecurity workshop",
        "software developer conference",
        "cloud computing workshop",
        "computer networking meetup",
        "DevOps conference",
        "robotics engineering workshop",
        "startup founder meetup",
        "entrepreneurship pitch night",
        "technology career fair",
        "hackathon",
    )

    def test_must_include_titles_still_include(self):
        for title in self.MUST_INCLUDE_TITLES:
            with self.subTest(title=title):
                result = classify_relevance(_event(title))
                self.assertEqual(result["decision"], "include", f"{title!r} -> {result}")


class AmbiguousReviewTests(unittest.TestCase):
    """The task's explicit ambiguous REVIEW examples."""

    def test_generic_business_networking(self):
        self.assertEqual(classify_relevance(_event("Generic Business Networking"))["decision"], "review")

    def test_general_leadership_event(self):
        self.assertEqual(classify_relevance(_event("General Leadership Event"))["decision"], "review")

    def test_broad_innovation_conference(self):
        self.assertEqual(classify_relevance(_event("Broad Innovation Conference"))["decision"], "review")

    def test_general_engineering_event_without_technical_context(self):
        self.assertEqual(classify_relevance(_event("General Engineering Event"))["decision"], "review")

    def test_investor_vc_event_with_unclear_audience(self):
        self.assertEqual(classify_relevance(_event("Investor/VC Event"))["decision"], "review")

    def test_professional_development_event_without_clear_relevance(self):
        self.assertEqual(classify_relevance(_event("Professional Development Event"))["decision"], "review")


class ClearlyUnrelatedExcludeTests(unittest.TestCase):
    """The task's explicit clearly-unrelated EXCLUDE examples."""

    def test_wellness_fitness(self):
        self.assertEqual(classify_relevance(_event("Evening Yoga and Wellness Session"))["decision"], "exclude")

    def test_arts_crafts(self):
        self.assertEqual(classify_relevance(_event("Community Pottery Class"))["decision"], "exclude")

    def test_unrelated_entertainment_social(self):
        self.assertEqual(classify_relevance(_event("Friday Night Trivia and Karaoke Night"))["decision"], "exclude")

    def test_unrelated_healthcare(self):
        self.assertEqual(classify_relevance(_event("Free Community Vaccination Clinic"))["decision"], "exclude")

    def test_k12_only(self):
        self.assertEqual(classify_relevance(_event("Elementary School Science Fair"))["decision"], "exclude")

    def test_unrelated_nonprofit_community_programming(self):
        self.assertEqual(classify_relevance(_event("Neighborhood Food Drive"))["decision"], "exclude")

    def test_sports_category_excludes(self):
        self.assertEqual(
            classify_relevance(_event("Sun Devil Soccer vs. North Carolina", categories=["Sports"]))["decision"],
            "exclude",
        )


class TopicAreaTests(unittest.TestCase):
    """One representative case per required topic area."""

    def test_cs_software(self):
        self.assertEqual(classify_relevance(_event("Intro to Computer Science"))["decision"], "include")

    def test_data(self):
        # bare "data" alone is weak/ambiguous - needs the qualifying phrase.
        self.assertEqual(classify_relevance(_event("Data Science Bootcamp"))["decision"], "include")

    def test_networking_computer_vs_career(self):
        computer = classify_relevance(_event("Computer Networking Fundamentals"))
        self.assertEqual(computer["decision"], "include")
        career = classify_relevance(_event("Alumni Networking Night"))
        self.assertEqual(career["decision"], "review")

    def test_robotics(self):
        self.assertEqual(classify_relevance(_event("Robotics Club Open House"))["decision"], "include")

    def test_hackathons(self):
        self.assertEqual(classify_relevance(_event("Spring Hackathon 2026"))["decision"], "include")

    def test_tech_careers(self):
        self.assertEqual(classify_relevance(_event("Tech Career Fair"))["decision"], "include")

    def test_startups(self):
        self.assertEqual(classify_relevance(_event("Startup Showcase"))["decision"], "include")

    def test_entrepreneurship(self):
        self.assertEqual(classify_relevance(_event("Entrepreneurship 101"))["decision"], "include")

    def test_product(self):
        # bare "product" is too generic to score; "product management" is the
        # qualifying phrase, and it's weak/ambiguous by itself.
        self.assertEqual(classify_relevance(_event("Product Management Basics"))["decision"], "review")


class AmbiguousKeywordFalsePositiveTests(unittest.TestCase):
    """The task's explicit "be careful with" ambiguous words - no naive
    substring matches."""

    def test_ai_does_not_match_arbitrary_words(self):
        self.assertEqual(classify_relevance(_event("Maintaining Campus Gardens"))["decision"], "review")

    def test_data_alone_is_not_decisive(self):
        result = classify_relevance(_event("Data Entry Volunteers Needed"))
        self.assertEqual(result["decision"], "review")

    def test_networking_event_is_not_computer_networking(self):
        result = classify_relevance(_event("Fall Networking Mixer"))
        self.assertEqual(result["decision"], "review")

    def test_cloud_does_not_match_cloudy(self):
        result = classify_relevance(_event("Cloudy Skies Photography Walk"))
        self.assertNotIn("weak:cloud", result["signals"])
        self.assertNotIn("tech:cloud", result["signals"])

    def test_security_alone_is_ambiguous_physical_or_cyber(self):
        # DETERMINISTIC: "Campus Security Job Fair" is a real job fair (career
        # relevance is real, hence "job fair" is scored as event-format
        # evidence), but "security" alone never means "cybersecurity" - it's
        # weak/ambiguous (could be physical campus security). "job fair" is
        # deliberately NOT a strong business phrase (unlike "career fair"):
        # making any bare "job fair" auto-include would also auto-include a
        # nursing job fair, a teaching job fair, etc., which is exactly the
        # false-positive this task's adversarial audit is about. With no
        # strong tech/business phrase present, this is REVIEW, not include -
        # a human decides whether "security" here means the target category.
        result = classify_relevance(_event("Campus Security Job Fair"))
        self.assertEqual(result["decision"], "review")
        self.assertNotIn("tech:cybersecurity", result["signals"])
        self.assertIn("weak:security", result["signals"])

    def test_cybersecurity_career_fair_clearly_includes(self):
        # Contrast case: "cybersecurity" is unambiguous (strong tech phrase),
        # so pairing it with "career fair" is a clean, decisive include -
        # unlike bare "security", which stays ambiguous above.
        result = classify_relevance(_event("Cybersecurity Career Fair"))
        self.assertEqual(result["decision"], "include")
        self.assertIn("tech:cybersecurity", result["signals"])

    def test_engineering_alone_is_ambiguous(self):
        result = classify_relevance(_event("Engineering Open House"))
        self.assertEqual(result["decision"], "review")

    def test_business_alone_is_ambiguous(self):
        result = classify_relevance(_event("Business Mixer"))
        self.assertEqual(result["decision"], "review")

    def test_innovation_alone_is_ambiguous(self):
        result = classify_relevance(_event("Innovation Talk"))
        self.assertEqual(result["decision"], "review")

    def test_ml_does_not_match_mls_soccer_league(self):
        result = classify_relevance(_event("MLS Game Watch Party"))
        self.assertNotIn("tech:ml", result["signals"])

    def test_plural_recruiters_still_matches_singular_phrase(self):
        result = classify_relevance(_event("Discovery Day", description="Meet our recruiters."))
        self.assertEqual(result["decision"], "include")
        self.assertIn("business:recruiter", result["signals"])


class WeakSignalCombinationTests(unittest.TestCase):
    """Weak/ambiguous and event-format evidence, no matter how much of it
    combines, can never reach include on its own - only a strong tech or
    business phrase can. See AdversarialFalsePositiveTests below for the
    real-world examples that drove this policy."""

    def test_many_weak_signals_alone_still_stay_review(self):
        # Would have scored 3 (three distinct weak matches capped*1) and
        # auto-included under the pre-audit model - now correctly review:
        # "cloud"/"data"/"networking"/"engineering"/"business"/"innovation"
        # are all weak/ambiguous on their own, so no amount of them alone is
        # convincing. (Deliberately comma-separated, not adjacent, so no
        # pair of them accidentally forms a real strong phrase like "network
        # engineering" - see test_strong_signal_plus_weak_corroboration_includes
        # for that contrast case.)
        result = classify_relevance(_event(
            "Cloud, Data, Networking, Engineering, Business, and Innovation Roundtable"
        ))
        self.assertEqual(result["decision"], "review")
        self.assertGreaterEqual(result["score"], 3)

    def test_two_weak_signals_alone_stay_review(self):
        result = classify_relevance(_event("Business and Innovation Roundtable"))
        self.assertEqual(result["decision"], "review")

    def test_weak_signal_plus_format_never_includes(self):
        # Also would have auto-included under the pre-audit model (weak x2 +
        # format = 3). Format words are topic-neutral, so this stays review.
        result = classify_relevance(_event("Cloud and Data Workshop for Business Teams"))
        self.assertEqual(result["decision"], "review")

    def test_format_alone_never_includes(self):
        result = classify_relevance(_event("Community Workshop"))
        self.assertEqual(result["decision"], "review")

    def test_strong_signal_plus_weak_corroboration_includes(self):
        # One strong tech/business phrase is always sufficient on its own,
        # and weak signals around it just add corroborating explainability.
        result = classify_relevance(_event("Cloud Computing and Data Engineering Workshop"))
        self.assertEqual(result["decision"], "include")


class ConflictAndCategoryTests(unittest.TestCase):
    def test_arts_category_with_weak_networking_is_review_not_silent_exclude(self):
        result = classify_relevance(_event("Networking Mixer at the Art Museum", categories=["Arts and Entertainment"]))
        self.assertEqual(result["decision"], "review")

    def test_pure_arts_no_conflict_excludes(self):
        result = classify_relevance(_event("Broadway Show Night", categories=["Arts and Entertainment"]))
        self.assertEqual(result["decision"], "exclude")

    def test_format_word_does_not_rescue_exclude(self):
        # "workshop" is topic-neutral - it must not turn a clean exclude into
        # a conflict/review just because it names an event shape.
        result = classify_relevance(_event("Craft Fair Pottery Workshop"))
        self.assertEqual(result["decision"], "exclude")

    def test_supporting_category_alone_is_review(self):
        result = classify_relevance(_event("SOLS Seminar Series", categories=["Science"]))
        self.assertEqual(result["decision"], "review")


class ExplainabilityTests(unittest.TestCase):
    def test_include_reports_matched_topics_and_score(self):
        result = classify_relevance(_event("Cybersecurity Workshop"))
        self.assertIn("cybersecurity", result["matched_topics"])
        self.assertGreaterEqual(result["score"], 3)
        self.assertIsInstance(result["reason"], str)
        self.assertTrue(result["signals"])

    def test_review_reports_zero_score_and_generic_reason(self):
        result = classify_relevance(_event("Maosho"))
        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["matched_topics"], [])

    def test_exclude_reports_no_matched_topics(self):
        result = classify_relevance(_event("Evening Yoga"))
        self.assertEqual(result["decision"], "exclude")
        self.assertEqual(result["matched_topics"], [])
        self.assertIn("exclude_keyword:yoga", result["signals"])

    def test_determinism(self):
        event = _event("Cybersecurity Workshop")
        self.assertEqual(classify_relevance(event), classify_relevance(event))


class AudienceTests(unittest.TestCase):
    def test_audience_alone_is_insufficient(self):
        result = classify_relevance(_event("Some Event", audiences=["Current ASU students"]))
        self.assertEqual(result["decision"], "review")

    def test_strong_signal_with_no_audience_still_includes(self):
        result = classify_relevance(_event("Career Fair", audiences=None))
        self.assertEqual(result["decision"], "include")


if __name__ == "__main__":
    unittest.main()

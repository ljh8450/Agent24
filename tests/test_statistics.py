import unittest

from app.contracts import Constraint, Variable
from app.statistics import estimate


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.variables = [
            Variable("region", "Region", ("daejeon", "other")),
            Variable("enrollment", "Enrollment", ("enrolled", "not_enrolled")),
            Variable("interest", "Interest", ("high", "low")),
        ]

    def constraint(self, identifier, where, value):
        return Constraint(identifier, identifier, "src_fixture", where, "eq", value, "exact", "fixture", "approved")

    def test_maximum_entropy_satisfies_margins_and_returns_identification_interval(self):
        constraints = [
            self.constraint("region", {"region": "daejeon"}, 0.2),
            self.constraint("enrollment", {"enrollment": "enrolled"}, 0.6),
            self.constraint("interest", {"interest": "high"}, 0.4),
            self.constraint("joint", {"region": "daejeon", "enrollment": "enrolled"}, 0.15),
        ]
        result = estimate(
            self.variables, constraints, {"interest": "high"}, {"region": "daejeon", "enrollment": "enrolled"}
        )
        self.assertEqual(result["status"], "feasible")
        self.assertAlmostEqual(result["maximum_entropy"]["point_estimate"], 0.4, places=6)
        self.assertLessEqual(result["identification"]["lower"], 0.4)
        self.assertGreaterEqual(result["identification"]["upper"], 0.4)
        self.assertEqual(result["maximum_entropy"]["algorithm"], "iterative_proportional_fitting")

    def test_conflicting_constraints_block_estimation(self):
        result = estimate(
            self.variables,
            [self.constraint("low", {"region": "daejeon"}, 0.2), self.constraint("high", {"region": "daejeon"}, 0.9)],
            {"region": "daejeon"},
            None,
        )
        self.assertEqual(result["status"], "infeasible")
        self.assertEqual(set(result["conflict_core"]), {"low", "high"})

    def test_zero_possible_denominator_is_not_given_a_fabricated_conditional_range(self):
        result = estimate(
            self.variables,
            [self.constraint("zero", {"region": "daejeon"}, 0.0)],
            {"interest": "high"},
            {"region": "daejeon"},
        )
        self.assertEqual(result["identification"]["status"], "undefined_due_to_zero_denominator")

    def test_user_declared_dag_is_a_separate_fitted_point_model(self):
        result = estimate(
            self.variables,
            [
                self.constraint("region", {"region": "daejeon"}, 0.2),
                self.constraint("interest", {"interest": "high"}, 0.4),
            ],
            {"interest": "high"},
            {"region": "daejeon"},
            [{"id": "interest_by_region", "parents": {"interest": ["region"]}}],
            "interest_by_region",
        )
        self.assertEqual(result["selected_model"], "interest_by_region")
        self.assertEqual(result["structure_sensitivity"][0]["status"], "fitted")


if __name__ == "__main__":
    unittest.main()


import numpy as np
import math
from scipy.optimize import linprog

class BlendingOptimizer:
    def __init__(self, tanks, specs, target_qty):
        self.tanks = tanks
        self.specs = specs
        self.target_qty = target_qty

    def get_viscosity_index(self, v_cst):
        """
        Refutas Viscosity Blending Index (VBI).
        using Excel's logic: log10(log10(v + 0.8))
        Commonly VBI = 14.534 * ln(ln(v + 0.8)) + 10.975
        But Excel J151 uses: J$24 * LOG10(LOG10(J29+0.8))
        J24 is Mass Fraction.
        So Component Index = LOG10(LOG10(v + 0.8)).
        Blend Index = Sum(x_i * Index_i) / Sum(x_i).
        
        Is it accurate?
        Common Refutas uses: VBN = 14.534 × ln(ln(v + 0.8)) + 10.975
        Excel uses just log10(log10).
        Since VBN is just a linear scaling of log10(log10), the linear blending property holds for both.
        We will use the Excel version: VBI = log10(log10(v + 0.8)).
        """
        if v_cst is None or v_cst <= 0:
            return 0
        return math.log10(math.log10(v_cst + 0.8))

    def get_flash_point_index(self, fp_deg_c):
        """
        Flash Point Index.
        Excel Row 154: 10^(57 - LOG(fp * 1.8 + 492)/0.05)
        Note: fp * 1.8 + 32 would be Fahrenheit? 
        492 = 460 + 32? Rankine?
        ASTM D2700?
        Let's stick to the formula:
        Term = LOG10(fp * 1.8 + 492)
        Exponent = (57 - Term / 0.05)
        Index = 10^Exponent
        
        Wait, earlier debug output F154 said:
        SUM(J154..)/Mass.
        J154 = Mass * 10^(57 - ...)
        So Index = 10^(57 - LOG10(fp * 1.8 + 492)/0.05).
        """
        if fp_deg_c is None:
            return 0
        try:
            term = math.log10(fp_deg_c * 1.8 + 492)
            exponent = 57 - (term / 0.05)
            return 10**exponent
        except:
            return 0

    def get_pour_point_index(self, pp_deg_c):
        """
        Pour Point Index.
        Excel Row 155: 10^(7 - (1350 / (pp + 273)))
        (pp + 273) is Kelvin.
        """
        if pp_deg_c is None:
            return 0
        try:
            return 10**(7 - (1350 / (pp_deg_c + 273)))
        except:
            return 0

    def optimize(self):
        """
        Solves the blending problem using Linear Programming.
        Variables: x_0, x_1, ... x_n (Mass of each tank).
        """
        num_tanks = len(self.tanks)
        
        # 1. Objective Function: Minimize Cost (or just Mass if no cost).
        # We start with just feasibility (c=0). 
        # But linprog needs c. Let's minimize total deviation or just use 0.
        # Actually we fix Total Mass constraint. So c doesn't matter much.
        # Let's use c = 1 (Minimize sum x). But sum x is constrained.
        c = np.ones(num_tanks)

        # 2. Equality Constraint: Sum(x) = Target Qty
        A_eq = [np.ones(num_tanks)]
        b_eq = [self.target_qty]

        # 3. Inequality Constraints (Au_ub * x <= b_ub)
        A_ub = []
        b_ub = []

        # Helper to add Max Constraint: Sum(x_i * p_i) <= Total * Spec
        # => Sum(x_i * (p_i - Spec)) <= 0
        def add_max_constraint(prop_getter, spec_val):
            if spec_val is None:
                return
            row = []
            for t in self.tanks:
                val = prop_getter(t['properties'])
                row.append(val - spec_val)
            A_ub.append(row)
            b_ub.append(0)

        # Helper to add Min Constraint: Sum(x_i * p_i) >= Total * Spec
        # => Sum(x_i * (Spec - p_i)) <= 0
        def add_min_constraint(prop_getter, spec_val):
            if spec_val is None:
                return
            row = []
            for t in self.tanks:
                val = prop_getter(t['properties'])
                row.append(spec_val - val)
            A_ub.append(row)
            b_ub.append(0)

        # Density (Linear)
        if 'density_max' in self.specs:
            add_max_constraint(lambda p: p.get('density', 0), self.specs['density_max'])

        # Sulphur (Linear)
        if 'sulphur_max' in self.specs:
            add_max_constraint(lambda p: p.get('sulphur', 0), self.specs['sulphur_max'])

        # Water (Linear)
        if 'water_max' in self.specs:
            add_max_constraint(lambda p: p.get('water', 0), self.specs['water_max'])

        # Viscosity (Refutas Index) - Max Spec
        if 'viscosity_max' in self.specs:
            spec_idx = self.get_viscosity_index(self.specs['viscosity_max'])
            row = []
            for t in self.tanks:
                v = t['properties'].get('viscosity', 0)
                idx = self.get_viscosity_index(v)
                row.append(idx - spec_idx)
            A_ub.append(row)
            b_ub.append(0)

        # Flash Point (Index) - Min Spec
        if 'flash_point_min' in self.specs:
            # Min Spec for Flash Point value.
            # Index Logic:
            # 10^(57 - ...)
            # Higher Flash Point = Lower Index (because LOG is subtracted).
            # So Min Flash Point = Max Index.
            # Let's verify monotonicity.
            # FP=60 -> 14.5. FP=100 -> 10.0. 
            # Yes, Higher FP = Lower Index.
            # So "Flash Point >= 60" means "Index <= Index(60)".
            spec_idx = self.get_flash_point_index(self.specs['flash_point_min'])
            row = []
            for t in self.tanks:
                fp = t['properties'].get('flash_point', 0)
                idx = self.get_flash_point_index(fp)
                # We want Blend Index <= Spec Index
                row.append(idx - spec_idx)
            A_ub.append(row)
            b_ub.append(0)

        # Pour Point (Index) - Max Spec
        if 'pour_point_max' in self.specs:
            # Index Logic: 10^(7 - ...)
            # Higher PP (-10 -> +10).
            # PP=-10 -> 1350/263=5.1. 7-5.1=1.9. 10^1.9=80.
            # PP=10 -> 1350/283=4.7. 7-4.7=2.3. 10^2.3=200.
            # Higher PP = Higher Index.
            # So Max PP = Max Index.
            spec_idx = self.get_pour_point_index(self.specs['pour_point_max'])
            row = []
            for t in self.tanks:
                pp = t['properties'].get('pour_point', 0)
                idx = self.get_pour_point_index(pp)
                row.append(idx - spec_idx)
            A_ub.append(row)
            b_ub.append(0)

        # Bounds (0 <= x_i <= Available)
        bounds = [(0, t['quantity']) for t in self.tanks]

        # Solve
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')

        if res.success:
            return {
                'success': True,
                'amounts': res.x.tolist(),
                'status': res.message
            }
        else:
            return {
                'success': False,
                'status': res.message,
                'amounts': np.zeros(num_tanks).tolist()
            }

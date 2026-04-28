
import numpy as np
import math
from scipy.optimize import linprog

class BlendingOptimizer:
    def __init__(self, blending_data, target_qty, custom_constraints=None, tank_usage_constraints=None):
        """
        blending_data: Result from excel.get_blending_data()
        custom_constraints: List of {spec, value, operator} from LLM
        tank_usage_constraints: List of {tank_id, operator, value}
        """
        self.tanks = blending_data['tanks']
        self.properties = blending_data['properties']
        self.target_qty = target_qty
        self.custom_constraints = custom_constraints or []
        self.tank_usage_constraints = tank_usage_constraints or []

    def _safe_float(self, val, default=0.0):
        """
        Robustly convert to float, handling dicts, None, strings, etc.
        """
        if val is None:
            return default
        if isinstance(val, dict):
            val = val.get('value', default)
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_viscosity_index(self, v_cst):
        v_cst = self._safe_float(v_cst)
        if v_cst <= 0: return 0
        try:
            return math.log10(math.log10(v_cst + 0.8))
        except:
            return 0

    def get_flash_point_index(self, fp_deg_c):
        fp_deg_c = self._safe_float(fp_deg_c)
        try:
            val = fp_deg_c * 1.8 + 492
            if val <= 0: return 0
            return 10**(57 - (math.log10(val) / 0.05))
        except:
            return 0
    
    def get_pour_point_index(self, pp_deg_c):
        pp_deg_c = self._safe_float(pp_deg_c)
        try:
            val = pp_deg_c + 273
            if val == 0: return 0
            return 10**(7 - (1350 / val))
        except:
            return 0

    def get_index_for_property(self, prop_name, value):
        """
        Returns the linear blending index for a given property value.
        """
        name_lower = prop_name.lower()
        if 'viscosity' in name_lower:
            return self.get_viscosity_index(value)
        elif 'flash point' in name_lower:
            return self.get_flash_point_index(value)
        elif 'pour point' in name_lower:
            return self.get_pour_point_index(value)
        else:
            return self._safe_float(value) # Linear

    def get_value_from_index(self, prop_name, index_val):
        """
        Reverse transform: Index -> Property Value (for display)
        Measurements are approximate for display.
        """
        name_lower = prop_name.lower()
        index_val = self._safe_float(index_val)
        
        try:
            if 'viscosity' in name_lower:
                if index_val <= 0: return 0
                return (10**(10**index_val)) - 0.8
            elif 'flash point' in name_lower:
                if index_val <= 0: return 0
                r = 10**((57 - math.log10(index_val)) * 0.05)
                return (r - 492) / 1.8
            elif 'pour point' in name_lower:
                if index_val <= 0: return 0
                k = 1350 / (7 - math.log10(index_val))
                return k - 273
            else:
                return index_val
        except:
            return 0


    def _build_bounds(self):
        """
        Build bounds for tanks, incorporating tank_usage_constraints.
        """
        bounds = []
        for t in self.tanks:
            capacity = self._safe_float(t.get('quantity'))
            lower = 0.0
            upper = capacity

            # Check for specific usage constraints
            for tc in self.tank_usage_constraints:
                if str(tc.get('tank_id')) == str(t['id']):
                    op = tc.get('operator', 'min').lower()
                    val = self._safe_float(tc.get('value'))
                    
                    if op == 'min':
                        lower = max(lower, val)
                    elif op == 'exact':
                        lower = val
                        upper = val
            
            bounds.append((lower, upper))
        return bounds


    def optimize_max_quantity(self):
        """
        Fallback Strategy A: Maximize Quantity with strict specs.
        Removes the target quantity equality constraint.
        Objective: Maximize Sum(x) -> Minimize Sum(-x)
        """
        num_tanks = len(self.tanks)
        if num_tanks == 0:
             return {'success': False, 'status': 'No tanks available', 'allocation': [], 'property_table': []}

        # 1. Objective: Minimize -Sum(x) for all tanks
        c = [-1.0] * num_tanks
        
        # 2. Equality Constraint: REMOVED ( We want max possible, not specific target)
        A_eq = None
        b_eq = None

        # 3. Inequality Constraints (Strict Specs) - Same as standard
        A_ub, b_ub = self._build_spec_constraints(relaxation_threshold=0.0)

        # 4. Bounds
        bounds = self._build_bounds()

        # 5. Solve
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')
        
        return self._format_result(res, "Max Possible Quantity")


    def optimize_closest_match(self):
        """
        Fallback Strategy B: Closest Spec Match.
        Objective: Minimize (Sum(Slack) * Penalty) + (Price * 0.001)
        Constraints:
          Sum(x) = Target Qty
          Prop_i(x) <= Spec_i + Slack_i  (for Max)
          Prop_i(x) >= Spec_i - Slack_i  (for Min)
          Slack_i >= 0
        """
        num_tanks = len(self.tanks)
        if num_tanks == 0:
             return {'success': False, 'status': 'No tanks available', 'allocation': [], 'property_table': []}

        # Identify active constraints
        constraints_list = self._get_active_constraints()
        num_constraints = len(constraints_list)
        
        # Dimensions:
        # x = [x_1, ..., x_n, s_1, ..., s_m]
        # n = num_tanks, m = num_constraints
        num_vars = num_tanks + num_constraints

        # 1. Objective: Minimize Slack (High Penalty) + Price (Low Weight)
        # Price minimizes cost slightly, but Slacks dominate.
        # Penalty: 1,000,000 per unit of deviation
        penalty_weight = 1000000.0
        price_weight = 0.001
        
        c = []
        # Cost coefficients for tanks
        for t in self.tanks:
            c.append(self._safe_float(t.get('price')) * price_weight)
        
        # Coefficients for Slacks (Penalty)
        c.extend([penalty_weight] * num_constraints)

        # 2. Equality Constraint: Sum(x_tanks) = Target Qty
        # [1, 1, ..., 0, 0] * [x..., s...] = Qty
        A_eq = []
        b_eq = []
        
        row_eq = [1.0] * num_tanks + [0.0] * num_constraints
        A_eq.append(row_eq)
        b_eq.append(self.target_qty)

        # 3. Inequality Constraints (Property Limits + Slacks)
        A_ub = []
        b_ub = []

        for i, constr in enumerate(constraints_list):
            prop_name = constr['name']
            op = constr['operator']
            limit_val = constr['value'] # Strict limit
            
            # Note: We rely on linear approximation here (index mixing)
            spec_index = self.get_index_for_property(prop_name, limit_val)
            prop_obj = next(p for p in self.properties if p['name'] == prop_name)
            
            row = []
            
            # Part A: Tank Coefficients [x_1 ... x_n]
            for t in self.tanks:
                val = self._safe_float(prop_obj.get('tank_values', {}).get(t['id']))
                idx = self.get_index_for_property(prop_name, val)
                
                # Standard Logic:
                # Max: Sum(x * idx) <= Sum(x * spec) + Slack
                #      Sum(x * (idx - spec)) - Slack <= 0
                
                if 'flash point' in prop_name.lower():
                     # Min Flash -> Treats like Max Index
                     # Max Flash -> Treats like Min Index
                     if op == 'min': coeff = idx - spec_index
                     else:           coeff = spec_index - idx
                else:
                    if op == 'min': coeff = spec_index - idx
                    else:           coeff = idx - spec_index
                
                row.append(coeff)
            
            # Part B: Slack Coefficients [s_1 ... s_m]
            # We want -Slack in the constraint inequality (lhs <= 0)
            # LHS - Slack <= 0
            # So coefficient for THIS slack is -1. Others are 0.
            slack_coeffs = [0.0] * num_constraints
            slack_coeffs[i] = -1.0
            
            row.extend(slack_coeffs)
            
            A_ub.append(row)
            b_ub.append(0)

        # 4. Bounds
        # Tanks: 0 to Capacity (Incorporating usage constraints)
        bounds = self._build_bounds()
        # Slacks: 0 to Infinity (Soft constraint violation)
        bounds.extend([(0, None)] * num_constraints)

        # 5. Solve
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')
        
        # 6. Format Result manually because dimensions changed
        result = self._format_result_expanded(res, num_tanks, "Closest Spec Match")
        
        return result

    def _get_active_constraints(self):
        """Helper to unify constraint gathering logic."""
        active_constraints = []
        # Defaults
        for prop in self.properties:
            if prop.get('is_spec') and prop.get('spec') is not None:
                prop_name = prop['name']
                is_min = 'flash point' in prop_name.lower()
                op = 'min' if is_min else 'max'
                active_constraints.append({
                    'name': prop_name,
                    'operator': op,
                    'value': self._safe_float(prop['spec']),
                    'type': 'default'
                })
        # Overrides
        for custom in self.custom_constraints:
            spec_name = custom.get('spec')
            op = custom.get('operator', 'max').lower()
            val = self._safe_float(custom.get('value'))
            target_prop = next((p for p in self.properties if p['name'] == spec_name), None)
            if not target_prop: continue
            real_name = target_prop['name']
            existing = next((x for x in active_constraints if x['name'] == real_name and x['operator'] == op), None)
            if existing:
                existing['value'] = val
            else:
                active_constraints.append({'name': real_name, 'operator': op, 'value': val})
        return active_constraints

    def _format_result_expanded(self, res, num_tanks, status_msg):
        """Special formatter for expanded variable set (tanks + slacks)."""
        result = {
            'success': res.success,
            'status': status_msg if res.success else res.message,
            'total_mass': 0, 'total_cost': 0, 'unit_price': 0,
            'tank_allocations': [], 'property_table': []
        }
        
        if res.success:
            # Slice only tank variables
            final_amounts = res.x[:num_tanks]
            result['total_mass'] = sum(final_amounts)
            
            total_cost = 0
            for i, t in enumerate(self.tanks):
                amt = final_amounts[i]
                total_cost += amt * self._safe_float(t.get('price'))
            
            result['total_cost'] = total_cost
            if result['total_mass'] > 0:
                result['unit_price'] = total_cost / result['total_mass']

            for i, t in enumerate(self.tanks):
                amt = final_amounts[i]
                if amt > 0.001:
                    result['tank_allocations'].append({
                        'tank_id': t['id'],
                        'tank_name': t['name'],
                        'amount': amt,
                        'percentage': (amt / result['total_mass']) * 100 if result['total_mass'] > 0 else 0,
                        'cost': amt * self._safe_float(t.get('price'))
                    })
            
            # Calculate Properties
            for prop in self.properties:
                prop_name = prop['name']
                numer = 0; denom = 0; tank_vals = {}
                for i, t in enumerate(self.tanks):
                    amt = final_amounts[i]
                    val = self._safe_float(prop['tank_values'].get(t['id']))
                    tank_vals[t['id']] = val
                    numer += amt * self.get_index_for_property(prop_name, val)
                    denom += amt
                
                # Determine operator
                is_min = 'flash point' in prop_name.lower()
                op = 'min' if is_min else 'max'
                for custom in self.custom_constraints:
                    if custom.get('spec') == prop_name:
                        op = custom.get('operator', op).lower()

                blend_val = self.get_value_from_index(prop_name, numer/denom) if denom > 0 else 0
                result['property_table'].append({
                    'name': prop_name,
                    'spec': prop.get('spec'),
                    'operator': op,
                    'safety_margin': self._safe_float(prop.get('safety_margin')),
                    'blend_value': blend_val,
                    'tank_values': tank_vals
                })
        
        return result


    def _build_spec_constraints(self, relaxation_threshold=0.0):
        """
        Helper to build A_ub and b_ub for property constraints, optionally relaxed.
        """
        A_ub = []
        b_ub = []
        
        # Build Constraint List
        active_constraints = []
        
        # A. Defaults from Excel
        for prop in self.properties:
            if prop.get('is_spec') and prop.get('spec') is not None:
                prop_name = prop['name']
                is_min = 'flash point' in prop_name.lower()
                op = 'min' if is_min else 'max'
                active_constraints.append({
                    'name': prop_name,
                    'operator': op,
                    'value': self._safe_float(prop['spec']),
                    'margin': self._safe_float(prop.get('safety_margin')),
                    'type': 'default'
                })

        # B. Merge Custom Constraints
        for custom in self.custom_constraints:
            spec_name = custom.get('spec')
            op = custom.get('operator', 'max').lower()
            val = self._safe_float(custom.get('value'))
            
            target_prop = next((p for p in self.properties if p['name'] == spec_name), None)
            if not target_prop: continue
            
            real_name = target_prop['name']
            existing = next((x for x in active_constraints if x['name'] == real_name and x['operator'] == op), None)
            
            if existing:
                existing['value'] = val
                existing['margin'] = 0.0
                existing['type'] = 'user_override'
            else:
                active_constraints.append({
                    'name': real_name,
                    'operator': op,
                    'value': val,
                    'margin': 0.0,
                    'type': 'user_new'
                })

        # C. Build Matrix with Relaxation
        for constr in active_constraints:
            prop_name = constr['name']
            op = constr['operator']
            limit_val = constr['value']
            margin = constr['margin']
            
            # Apply Safety Margin AND Relaxation
            # Relaxation expands the feasible region.
            # Max: Limit increases.
            # Min: Limit decreases.
            
            margin_factor = margin / 100.0
            
            if op == 'min':
                # Original: >= limit * (1 + margin)
                # Relaxed:  >= limit * (1 + margin) * (1 - relaxation)
                effective_spec = limit_val * (1.0 + margin_factor) * (1.0 - relaxation_threshold)
            else:
                # Original: <= limit * (1 - margin)
                # Relaxed:  <= limit * (1 - margin) * (1 + relaxation_threshold)
                effective_spec = limit_val * (1.0 - margin_factor) * (1.0 + relaxation_threshold)
            
            spec_index = self.get_index_for_property(prop_name, effective_spec)
            prop_obj = next(p for p in self.properties if p['name'] == prop_name)
            
            row = []
            for t in self.tanks:
                raw_val = prop_obj.get('tank_values', {}).get(t['id'])
                val = self._safe_float(raw_val)
                idx = self.get_index_for_property(prop_name, val)
                
                # Logic same as before
                if 'flash point' in prop_name.lower():
                    if op == 'min': row.append(idx - spec_index)
                    else:           row.append(spec_index - idx)
                else:
                    if op == 'min': row.append(spec_index - idx)
                    else:           row.append(idx - spec_index)
            
            A_ub.append(row)
            b_ub.append(0)
            
        return A_ub, b_ub

    def _format_result(self, res, success_status_msg="Optimization Successful"):
        """
        Helper to format linprog result into the standard dictionary.
        """
        result = {
            'success': res.success,
            'status': success_status_msg if res.success else res.message,
            'total_mass': 0,
            'total_cost': 0,
            'unit_price': 0,
            'tank_allocations': [],
            'property_table': []
        }
        
        if res.success:
            final_amounts = res.x
            result['total_mass'] = sum(final_amounts)
            
            total_cost = 0
            for i, t in enumerate(self.tanks):
                amt = final_amounts[i]
                price = self._safe_float(t.get('price'))
                total_cost += amt * price
            result['total_cost'] = total_cost
            if result['total_mass'] > 0:
                result['unit_price'] = total_cost / result['total_mass']

            for i, t in enumerate(self.tanks):
                amt = final_amounts[i]
                if amt > 0.001:
                    result['tank_allocations'].append({
                        'tank_id': t['id'],
                        'tank_name': t['name'],
                        'amount': amt,
                        'percentage': (amt / result['total_mass']) * 100 if result['total_mass'] > 0 else 0,
                        'cost': amt * self._safe_float(t.get('price'))
                    })
            
            # Recalculate properties (same logic as before)
            # To avoid code duplication, we could extract this too, but for now copying the loop is safer
            # to preserve context of self.properties
            active_constraints = [] # We need to re-derive active constraints for display or just show calc values
            # For display purposes, we just calculate the blend values.
            
            for prop in self.properties:
                prop_name = prop['name']
                numer = 0
                denom = 0
                tank_vals_formatted = {}
                for i, t in enumerate(self.tanks):
                    amt = final_amounts[i]
                    val = self._safe_float(prop['tank_values'].get(t['id']))
                    tank_vals_formatted[t['id']] = val
                    idx = self.get_index_for_property(prop_name, val)
                    numer += amt * idx
                    denom += amt
                
                if denom > 0:
                    blend_val = self.get_value_from_index(prop_name, numer/denom)
                else:
                    blend_val = 0
                
                # Determine operator
                is_min = 'flash point' in prop_name.lower()
                op = 'min' if is_min else 'max'
                for custom in self.custom_constraints:
                    if custom.get('spec') == prop_name:
                        op = custom.get('operator', op).lower()

                result['property_table'].append({
                    'name': prop_name,
                    'spec': prop.get('spec'), # Show original spec
                    'operator': op,
                    'safety_margin': self._safe_float(prop.get('safety_margin')),
                    'blend_value': blend_val,
                    'tank_values': tank_vals_formatted
                })
        else:
             if 'infeasible' in str(res.message).lower():
                 result['status'] += " (Constraints could not be met)"
        
        return result

    def optimize(self):
        """
        Original method refactored to use helpers.
        """
        num_tanks = len(self.tanks)
        if num_tanks == 0:
             return {'success': False, 'status': 'No tanks available', 'allocation': [], 'property_table': []}

        # 1. Objective: Minimize Cost
        c = [self._safe_float(t.get('price')) for t in self.tanks]
        if sum(c) == 0: c = np.ones(num_tanks)
        
        # 2. Equality Constraint: Sum(x) = Target Qty
        A_eq = [np.ones(num_tanks)]
        b_eq = [self.target_qty]

        # 3. Inequality Constraints (Strict)
        A_ub, b_ub = self._build_spec_constraints(relaxation_threshold=0.0)

        # 4. Bounds
        bounds = self._build_bounds()

        # 5. Solve
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')
        
        return self._format_result(res)


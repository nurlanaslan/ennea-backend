import re
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from .services.excel import get_blending_data
from .services.blending import BlendingOptimizer
from .models import ChatSession, ChatMessage, Terminal
import os
import json
import openai


class TankListView(APIView):
    def get(self, request):
        included = request.query_params.getlist('included_terminals')
        excluded = request.query_params.getlist('excluded_terminals')
        data = get_blending_data(
    included_terminals=included,
     excluded_terminals=excluded)
        if not data:
            return Response({"error": "Could not read Excel data"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(data)


class CalculateBlendView(APIView):
    def post(self, request):
        target_qty = request.data.get('quantity')
        if not target_qty:
            return Response({"error": "Quantity required"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            qty = float(target_qty)
        except ValueError:
            return Response({"error": "Invalid quantity"},
                            status=status.HTTP_400_BAD_REQUEST)
        blending_data = get_blending_data()
        if not blending_data:
             return Response({"error": "Could not read Excel data"},
                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        optimizer = BlendingOptimizer(blending_data, qty)
        result = optimizer.optimize()
        return Response(result)


class ChatHistoryView(APIView):
    def get(self, request, session_id=None):
        if session_id:
            # Retrieve single session
            session = get_object_or_404(ChatSession, id=session_id)
            messages = session.messages.order_by(
                'created_at').values('role', 'content', 'data')
            return Response({
                "id": session.id,
                "title": session.title,
                "messages": list(messages)
            })
        else:
            # List all sessions
            sessions = ChatSession.objects.all().order_by(
                '-updated_at').values('id', 'title', 'created_at')
            return Response(list(sessions))

    def post(self, request):
        # Create new session
        session = ChatSession.objects.create(title="New Chat")
        return Response({"id": session.id, "title": session.title})

    def delete(self, request, session_id):
        session = get_object_or_404(ChatSession, id=session_id)
        session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def parse_numeric(val):
    if val is None: return None
    if isinstance(val, (int, float)): return float(val)
    # Extract number from string like "500 MT" or "15,000"
    matches = re.findall(r"[-+]?\d*\.?\d+", str(val).replace(',', ''))
    return float(matches[0]) if matches else None


class ChatView(APIView):
    def post(self, request):
        user_message = request.data.get('message')
        session_id = request.data.get('session_id')
        if not user_message:
            return Response({"error": "Message required"},
                            status=status.HTTP_400_BAD_REQUEST)
        # Get or Create Session
        if session_id:
            session = get_object_or_404(ChatSession, id=session_id)
        else:
            session = ChatSession.objects.create(
                title=user_message[:30] + "...")
        # Save User Message
        ChatMessage.objects.create(
    session=session,
    role='user',
     content=user_message)
        # Check Mode
        mode = request.data.get('mode', 'Blend Table')
        if mode != 'Blend Table':
             response_text = f"This functionality {mode} is not available yet. Stay tuned for future updates!"
             calc_result = None
             # Save Assistant Message
             ChatMessage.objects.create(
    session=session,
    role='assistant',
     content=response_text)
             return Response({
                "session_id": session.id,
                "role": "assistant",
                "content": response_text,
                "data": None
            })
        # OpenAI Logic (Only if mode == Blend Table)
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
             return Response({"error": "OpenAI API Key not configured"},
                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        # Fetch Context Data (Tanks & Specs)
        terminals = Terminal.objects.all()
        terminal_names = [t.name for t in terminals]
        # Get data without terminal filters first to let AI see everything
        # available
        blending_data = get_blending_data()
        if not blending_data:
             return Response({"error": "Could not read Excel data for context"},
                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        tank_names = [f"{t['name']} ({t.get('quantity', 0)} MT, ${t.get('price', 'N/A')})" for t in blending_data['tanks']]
        spec_names = [p['name'] for p in blending_data['properties']]
        client = openai.OpenAI(api_key=api_key)
        # 1. Extract Intent, Quantity, Filters, and Overrides
        system_prompt = f"""
        You are an AI assistant for an Oil & Gas blending application.
        The user wants to blend tanks to meet a target quantity.
        Available Terminals: {", ".join(terminal_names) if terminal_names else "Default"}
        Available Tanks: {", ".join(tank_names)}
        Available Specs: {", ".join(spec_names)}
        1. If the user is asking for a calculation.
        2. The target quantity (e.g., "500 MT").
        3. Which tanks to INCLUDE (specific subset).
        4. Which tanks to EXCLUDE.
        5. Which terminals to INCLUDE (e.g. "Use only Terminal 1").
        6. Which terminals to EXCLUDE.
        7. Any specific property specifications to OVERRIDE (e.g. "max sulphur 0.5").
        8. TANK USAGE CONSTRAINTS:
            - "Empty Tank A" or "Use all of Tank A" -> Operator: "exact", Value: (Tank A's full stock).
            - "Use at least X from Tank A" -> Operator: "min", Value: X.
        9. Is the user asking "how much of X can we make?" WITHOUT specifying a target quantity?
           (e.g. "tankda ne qeder VLSFO var?", "ne qeder 0.5 cargo hazırlaya bilərik?",
            "how much VLSFO can we make?", "tanklarda ne qeder ULSFO hazırlamaq olar?")
           If YES: set is_max_quantity_query: true and quantity: null.
           If they mention a fuel grade (VLSFO, ULSFO, LSFO, HSFO), also populate `constraints` with the corresponding sulphur limit.
        IMPORTANT:
        - The user may write in Azerbaijani (or English). You must understand the intent in either language.
        - AMBIGUITY HANDLING: If the user refers to a tank name by a generic name (e.g. "Tank 1") that exists in multiple terminals, you MUST list those terminals in `ambiguous_tanks` and ask for clarification in `response_text`. Do NOT proceed with a calculation in this case.
        - You must ALWAYS return valid JSON confirming to the schema below.
        - CONTEXT AWARENESS: You are part of an ongoing conversation. If the user says "Change quantity to 1000", they imply keeping the previous filters unless they explicitly change them.
        - If the user sends a new instruction that modifies only one parameter, RETAIN the other parameters from the previous successful calculation.
        LANGUAGE NOTES & GRAMMAR (Azerbaijani):
        - IMPORTANT: Azerbaijani uses suffixes like "-u", "-ü", "-ı", "-i", "-da", "-də", "-ki", "-dakı".
        - "Terminal A-dakı Tank 1" means "Tank 1 in Terminal A".
        - "boşalt", "bitir", "tam boşalt", "boşaldılsın" -> TANK USAGE: exact (stock amount).
        - "ən azı", "minimum", "az olmayan", "milyon tondan az olmayaraq" -> TANK USAGE: min.
        - "qatmadan", "istifadə etmədən", "olmadan", "çıxmaqla", "xaric" -> EXCLUDE.
        - "istifadə edərək", "ilə", "yalnız", "təkcə" -> INCLUDE.
        - "ULSFO" -> Ultra Low Sulphur: Max Sulphur 0.1.
        - "VLSFO" -> Very Low Sulphur: Max Sulphur 0.5 (and Min Sulphur 0.1 if requested/logical).
        - "LSFO" -> Low Sulphur: Max Sulphur 1.0 (and Min Sulphur 0.5).
        - "HSFO" -> High Sulphur: Min Sulphur 1.0.
        - "hazırla", "qarışdır", "hazırlansın" -> START CALCULATION.
        
        TERMINAL vs TANK CONTEXT:
        - If the user says "Terminal A-dakı Tank 1", do NOT automatically add "Terminal A" to `included_terminals` unless they say "Only use Terminal A". Using a specific tank from a terminal does not mean excluding other terminals for the rest of the blend.
        
        TANK NAME MATCHING:
        - Use the EXACT names provided in 'Available Tanks' (including the terminal in parentheses).
        - If the user says "Terminal A-dakı Tank 1", you must provide the full name like "Tank 1 (Terminal A)" in `usage_constraints`.
        Return JSON ONLY:
        {{
            "is_calculation": true/false,
            "is_max_quantity_query": true/false,
            "quantity": number or null,
            "included_tankers": ["Exact Tank Name 1", ...],
            "excluded_tankers": ["Exact Tank Name 1", ...],
            "included_terminals": ["Exact Terminal Name 1", ...],
            "excluded_terminals": ["Exact Terminal Name 1", ...],
            "constraints": [
                {{ "spec": "Exact Spec Name", "value": number,
                    "operator": "min" or "max" }}
            ],
            "usage_constraints": [
                {{ "tank_name": "Tank Name", "value": number,
                    "operator": "min" or "exact" }}
            ],
            "ambiguous_tanks": [
                {{ "name": "Generic Name", "terminals": [
                    "Terminal A", "Terminal B"] }}
            ],
            "response_text": "Clarification or acknowledgement text",
            "success_template": "Result message template (Azerbaijani/English)",
            "failure_template": "Failure message template (Azerbaijani/English)"
        }}
        """
        try:
            # Fetch History (Last 10 messages, excluding current one which is already saved but we need to format it)
            # Actually, we just saved the current message above.
            # Let's fetch all messages for this session, ordered by time.
            # We will use them as context.
            # Get the latest 20 messages, then reverse to maintain
            # chronological order
            history_messages = reversed(ChatMessage.objects.filter(
                # Limit context window
                session=session).order_by('-created_at')[:20])
            formatted_history = []
            for msg in history_messages:
                # Skip the current message we just added to avoid duplication in logic if we want,
                # OR just use all of them and don't pass 'user_message' explicitly in the messages list
                # (but we usually want the system prompt first).
                # Better approach:
                # 1. System Prompt
                # 2. History (User/Assistant)
                # 3. Current Message (if not in history query yet? It IS in
                # history query because we created it).
                role = "user" if msg.role == 'user' else "assistant"
                formatted_history.append(
                    {"role": role, "content": msg.content or ""})
            # Construct full message chain
            messages_payload = [{"role": "system",
     "content": system_prompt}] + formatted_history
            # Note: 'user_message' is already in formatted_history because we did ChatMessage.objects.create() above.
            # So we don't need to append it again.
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=messages_payload,
                response_format={"type": "json_object"}
            )
            ai_content = completion.choices[0].message.content
            ai_json = json.loads(ai_content)
            print(f"DEBUG AI Response: {ai_content}")
            # print(ai_json)
            response_text = ai_json.get("response_text", "")
            is_calc = ai_json.get("is_calculation", False)
            qty = parse_numeric(ai_json.get("quantity"))
            included_tanks = ai_json.get("included_tankers") or []
            excluded_tanks = ai_json.get("excluded_tankers") or []
            included_terminals = ai_json.get("included_terminals") or []
            excluded_terminals = ai_json.get("excluded_terminals") or []
            constraints = ai_json.get("constraints") or []
            usage_constraints = ai_json.get("usage_constraints") or []
            ambiguous = ai_json.get("ambiguous_tanks") or []
            is_max_qty_query = ai_json.get("is_max_quantity_query", False)
            # Legacy support for overridden_specs if LLM uses old format
            overridden = ai_json.get("overridden_specs", [])
            if overridden and not constraints:
                for o in overridden:
                    constraints.append({
                        "spec": o.get("spec"),
                        "value": o.get("value"),
                        "operator": "max"  # Default assumption for legacy
                    })
            calc_result = None
            if (is_calc and qty) or is_max_qty_query:
                # --- Apply Filters ---
                # Re-fetch data with terminal filters if AI extracted them
                if included_terminals or excluded_terminals:
                    blending_data = get_blending_data(
    included_terminals=included_terminals,
     excluded_terminals=excluded_terminals)
                if not blending_data:
                    # This might happen if AI extracts a terminal name that
                    # doesn't exist
                    blending_data = get_blending_data()  # Fallback
                # --- Handle Ambiguity ---
                if ambiguous:
                    # The AI has already identified ambiguity.
                    # We skip calculation and return the AI's clarification
                    # request.
                    return Response({
                        "response_text": response_text,
                        "calc_result": None
                    })
                # 1. Filter Tanks
                original_tanks = blending_data['tanks']
                filtered_tanks = []
                if included_tanks:
                    for t in original_tanks:
                        # Support partial match since terminal name is in
                        # parentheses
                        if any(it in t['name'] for it in included_tanks):
                            filtered_tanks.append(t)
                else:
                    filtered_tanks = list(original_tanks)
                if excluded_tanks:
                    filtered_tanks = [
    t for t in filtered_tanks if not any(
        et in t['name'] for et in excluded_tanks)]
                blending_data['tanks'] = filtered_tanks
                # 1.5 Map Tank Usage Constraints
                mapped_usage_constraints = []
                for uc in usage_constraints:
                    t_name = uc.get('tank_name')
                    # Find the tank in filtered_tanks
                    target_tank = next(
    (t for t in filtered_tanks if t_name in t['name']), None)
                    if target_tank:
                        op = uc.get('operator', 'min')
                        raw_val = uc.get('value')
                        if raw_val is not None:
                            try:
                                val = parse_numeric(raw_val)
                            except:
                                val = 0.0
                        else:
                            val = float(
    target_tank.get(
        'quantity',
         0)) if op == 'exact' else 0.0
                        # FEASIBILITY CHECK: If emptying (exact) and stock >
                        # qty
                        if op == 'exact' and val > float(qty):
                            return Response({
                                "response_text": f"Requested quantity of the cargo ({qty} MT) is less than the tank's available cargo ({val} MT), so we cannot empty {t_name}.",
                                "calc_result": {"success": False, "status": "Infeasible: Tank volume exceeds blend quantity"}
                            })
                        mapped_usage_constraints.append({
                            "tank_id": target_tank['id'],
                            "operator": op,
                            "value": val
                        })
                # 2. Run Optimization with Custom Constraints
                # Note: We do NOT modify blending_data['properties'] directly anymore.
                # We pass the constraints to the optimizer.
                # Helper to run optimization for a specific set of tanks

                def solve_scenario(
    tanks_subset,
    constraints,
    usage_constraints,
    quantity,
     name):
                    subset_data = {
                        'tanks': tanks_subset,
                        'properties': blending_data['properties']
                    }
                    opt = BlendingOptimizer(
    subset_data, float(quantity), constraints, usage_constraints)
                    res = opt.optimize()
                    if res['success']:
                        res['scenario_name'] = name
                        res['tank_headers'] = [{'id': t['id'], 'name': t['name'], 'price': t.get(
                            'price', 0), 'terminal': t.get('terminal')} for t in tanks_subset]
                        res['tanks_used_count'] = len(tanks_subset)
                    return res
                results = []
                from itertools import combinations

                # --- NEW: Direct "How much can we make?" path ---
                if is_max_qty_query and not qty:
                    subset_data_all = {
                        'tanks': filtered_tanks,
                        'properties': blending_data['properties']
                    }
                    # target_qty=0 is ignored by optimize_max_quantity (no equality constraint)
                    opt_max = BlendingOptimizer(
                        subset_data_all, 0, constraints, mapped_usage_constraints)
                    res_max = opt_max.optimize_max_quantity()
                    if res_max['success'] and res_max['total_mass'] > 0:
                        res_max['scenario_name'] = "Max Available Quantity"
                        res_max['tank_headers'] = [
                            {'id': t['id'], 'name': t['name'],
                             'price': t.get('price', 0), 'terminal': t.get('terminal')}
                            for t in filtered_tanks
                        ]
                        res_max['tanks_used_count'] = len(
                            [t for t in res_max['tank_allocations'] if t['amount'] > 0])
                        res_max['is_max_query'] = True
                        results = [res_max]
                        calc_result = res_max
                    else:
                        calc_result = {'success': False,
                                       'status': 'No feasible blend found with the given constraints.'}

                # --- EXISTING: Fixed-quantity path (base + N-1 scenarios) ---
                else:
                # A. Base Scenario (All active tanks)
                 res_base = solve_scenario(
    filtered_tanks,
    constraints,
    mapped_usage_constraints,
    qty,
     "Optimal Blend (All Available)")
                 if res_base['success']:
                    results.append(res_base)
                    # Smart Filtering: Identify which tanks were ACTUALLY used in the base case
                    # If a tank was not used (mass=0), excluding it won't
                    # change the result, so we skip that scenario.
                    base_active_tank_ids = {str(t.get('tank_id') or t.get('id')) for t in res_base.get('tank_allocations', []) if t.get('amount', 0) > 0.001}
                    # B. N-1 Scenarios (Sensitivity Analysis)
                    if len(filtered_tanks) > 1:
                        restricted_tank_ids = {str(uc['tank_id']) for uc in mapped_usage_constraints}
                        
                        for combo in combinations(filtered_tanks, len(filtered_tanks) - 1):
                            combo_list = list(combo)
                            missing_tank = next((t for t in filtered_tanks if t not in combo_list), None)
                            if missing_tank:
                                m_id = str(missing_tank['id'])
                                # 1. If tank is forced by constraints, don't show "Without" it
                                if m_id in restricted_tank_ids:
                                    continue
                                # 2. If tank wasn't even used in base case, "Without" is redundant
                                if m_id not in base_active_tank_ids:
                                    continue
                                
                                res_sub = solve_scenario(combo_list, constraints, mapped_usage_constraints, qty, f"Without {missing_tank['name']}")
                                if res_sub['success']:
                                    results.append(res_sub)
                 else:
                    # Base Scenario FAILED. The request is infeasible with strict constraints.
                    # Trigger Fallback Strategies using ALL filtered tanks.
                    subset_data_all = {
                        'tanks': filtered_tanks,
                        'properties': blending_data['properties']
                    }
                    opt_fallback = BlendingOptimizer(
    subset_data_all, float(qty), constraints, mapped_usage_constraints)
                    # Fallback 1: Maximize Quantity (Strict Specs)
                    res_max = opt_fallback.optimize_max_quantity()
                    if res_max['success'] and res_max['total_mass'] > 0:
                        res_max['scenario_name'] = f"Max Quantity {res_max['total_mass']:.0f} MT"
                        res_max['tank_headers'] = [{'id': t['id'], 'name': t['name'], 'price': t.get(
                            'price', 0)} for t in filtered_tanks]
                        res_max['tanks_used_count'] = len(
                            [t for t in res_max['tank_allocations'] if t['amount'] > 0])
                        res_max['is_fallback'] = True
                        results.append(res_max)
                    # Fallback 2: Closest Spec Match (Target Quantity)
                    # Replaces naive relaxed specs
                    res_closest = opt_fallback.optimize_closest_match()
                    if res_closest['success']:
                        res_closest['scenario_name'] = "Closest Spec Match"
                        res_closest['tank_headers'] = [{'id': t['id'], 'name': t['name'], 'price': t.get(
                            'price', 0)} for t in filtered_tanks]
                        res_closest['tanks_used_count'] = len(
                            [t for t in res_closest['tank_allocations'] if t['amount'] > 0])
                        res_closest['is_fallback'] = True
                        results.append(res_closest)
                # Deduplication and sorting only apply to fixed-quantity path
                # (max-query path already set results and calc_result above)
                if not is_max_qty_query:
                    unique_results = []
                    seen_fingerprints = set()
                    for res in results:
                        cost_fp = round(res['total_cost'], 4)
                        used_tanks_fp = tuple(sorted([t['tank_id'] for t in res.get(
                            'tank_allocations', []) if t.get('amount', 0) > 0]))
                        fingerprint = (cost_fp, used_tanks_fp)
                        if fingerprint not in seen_fingerprints:
                            seen_fingerprints.add(fingerprint)
                            unique_results.append(res)
                    results = unique_results

                    # Prioritize results that match the target quantity
                    def sorting_key(res):
                        qty_diff = abs(res['total_mass'] - float(qty))
                        priority = 0 if qty_diff < 1.0 else 1
                        return (priority, qty_diff, res['total_cost'])

                    results.sort(key=sorting_key)

                    # Assign calc_result for downstream compatibility
                    calc_result = results[0] if results else res_base
                # Append calc result details to text
                if calc_result['success']:
                    best_result = calc_result
                    # Calculate Unit Price (Absolute value to handle negative cost representation)
                    # If total_mass is 0 (unlikely for success), default to 0
                    total_mass = best_result['total_mass']
                    total_cost = best_result['total_cost']
                    unit_price = (
    total_cost / total_mass) if total_mass > 0 else 0
                    calc_result_payload = {
                        'success': True,
                        'scenarios': results,
                        'total_cost': total_cost,
                        'total_mass': total_mass,
                        'unit_price': unit_price,  # NEW
                        'tank_allocations': best_result['tank_allocations'],
                        'property_table': best_result['property_table'],
                        'tank_headers': [{'id': t['id'], 'name': t['name'], 'price': t.get('price', 0), 'terminal': t.get('terminal')} for t in blending_data['tanks']]
                    }
                    # --- Response text: max-quantity query gets its own message ---
                    if best_result.get('is_max_query'):
                        response_text = (
                            f"Mövcud tanklar və tələb olunan spesifikasiyalara əsasən "
                            f"maksimum {total_mass:.0f} MT kargo hazırlana bilər."
                        )
                    else:
                        # Use Localized Template
                        tmpl = ai_json.get(
    'success_template',
     'Optimization successful for {qty} MT.')
                        try:
                            # Backward compat: if template still has {cost}, we provide it, but prefer {price}
                            response_text = tmpl.format(qty=qty, cost=f"{best_result['total_cost']:.2f}", price=f"{unit_price:.2f}")
                            
                            # Add explanation for fallbacks
                            is_fallback = any(r.get('is_fallback') for r in results if r == best_result)
                            if is_fallback:
                                if best_result['scenario_name'] == 'Closest Spec Match':
                                    response_text += "\n\nNOTE: Strict specifications could not be met for the exact quantity. Showing best possible match."
                                elif 'Max Quantity' in best_result['scenario_name']:
                                    response_text += f"\n\nNOTE: The requested {qty} MT is infeasible with these tanks/specs. Showing maximum possible quantity."
                        except:
                            # Fallback if format fails
                            response_text = f"Optimization successful for {qty} MT."
                    if len(results) > 1:
                        pass # The frontend might be handling 'Found X Options'. Let's check what the user wants. We'll append 'Found {len(results)} Options' but maybe with a space.
                    if included_tanks: response_text += f" (Included: {', '.join(included_tanks)})"
                    if excluded_tanks: response_text += f" (Excluded: {', '.join(excluded_tanks)})"
                else:
                    # Optimization Failed
                    calc_result_payload = calc_result
                    failure_reason = calc_result['status']
                    # Summarize Context for the AI explainer
                    # We need to send relevant tank specs to help parsing
                    # Only send specs that might be relevant or all?
                    # Let's send a simplified view of tanks and their properties
                    context_summary = []
                    for t in blending_data['tanks']:
                        # limited props
                        props_str = ", ".join([f"{p['name']}: {p['tank_values'].get(t['id'], 'N/A')}" for p in blending_data['properties'] if p['is_spec']])
                        context_summary.append(f"- {t['name']}: {props_str}")
                    context_str = "\n".join(context_summary)
                    explainer_prompt = f"""
                    The user asked: "{user_message}"
                    The optimization FAILED with status: "{failure_reason}"
                    Available Tank Context:
                    {context_str}
                    Target Quantity: {qty}
                    TASK:
                    Explain to the user in Azerbaijani WHY this blend is impossible.
                    - Analyze the 'status' to see which constraint failed (e.g. Sulphur).
                    - Look at the tank context. Identify which tanks have high/low values that cause this.
                    - Be helpful and specific. (e.g. "Tank 1-də kükürd çox yüksəkdir (0.9), amma siz maksimum 0.5 istəyirsiniz. Digər tankerlər bunu kompensasiya edə bilmir.")
                    - Keep it concise.
                    """
                    try:
                        explainer_completion = client.chat.completions.create(
                            model="gpt-4o",
                            messages=[
                                {"role": "system", "content": "You are an expert Oil & Gas blending assistant. Explain calculation failures clearly in Azerbaijani."},
                                {"role": "user", "content": explainer_prompt}
                            ]
                        )
                        response_text = explainer_completion.choices[0].message.content
                    except Exception as ex:
                        # Fallback
                        tmpl = ai_json.get('failure_template', 'Optimization failed')
                        response_text = f"{tmpl}: {failure_reason}"
            # Save Assistant Message
            ChatMessage.objects.create(
                session=session,
                role='assistant',
                content=response_text,
                data=calc_result_payload
            )
            # Update Session Title if it's the first real message and generic title
            if session.messages.count() <= 2 and session.title == "New Chat":
                session.title = user_message[:30] + "..."
                session.save()
            return Response({
                "session_id": session.id,
                "role": "assistant",
                "content": response_text,
                "data": calc_result_payload
            })
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
class TerminalManagementView(APIView):
    def get(self, request):
        terminals = Terminal.objects.all().order_by('name')
        data = []
        for t in terminals:
            file_info = None
            if t.excel_file and os.path.exists(t.excel_file.path):
                try:
                    stats = os.stat(t.excel_file.path)
                    file_info = {
                        'name': os.path.basename(t.excel_file.name),
                        'size': stats.st_size,
                        'last_modified': stats.st_mtime
                    }
                except:
                    file_info = {'name': os.path.basename(t.excel_file.name), 'error': 'File missing'}
            data.append({
                'id': t.id,
                'name': t.name,
                'file': file_info,
                'created_at': t.created_at
            })
        return Response(data)
    def post(self, request):
        name = request.data.get('name')
        if not name:
            return Response({"error": "Terminal name required"}, status=status.HTTP_400_BAD_REQUEST)
        terminal, created = Terminal.objects.get_or_create(name=name)
        return Response({
            "id": terminal.id,
            "name": terminal.name,
            "created": created
        })
    def delete(self, request, terminal_id):
        terminal = get_object_or_404(Terminal, id=terminal_id)
        # Delete the file too if it exists
        if terminal.excel_file:
             if os.path.exists(terminal.excel_file.path):
                os.remove(terminal.excel_file.path)
        terminal.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
class TerminalUploadView(APIView):
    def post(self, request, terminal_id):
        terminal = get_object_or_404(Terminal, id=terminal_id)
        if 'file' not in request.FILES:
             return Response({"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)
        uploaded_file = request.FILES['file']
        if not uploaded_file.name.endswith('.xlsx'):
             return Response({"error": "Invalid file type"}, status=status.HTTP_400_BAD_REQUEST)
        # Save file to terminal
        terminal.excel_file = uploaded_file
        terminal.save()
        return Response({"success": True, "message": f"File uploaded for {terminal.name}"})

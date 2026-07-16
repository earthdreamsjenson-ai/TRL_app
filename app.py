import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import random
from datetime import datetime, timedelta
import io
from collections import Counter

st.set_page_config(page_title="TRL日程管理", layout="wide")
st.title("⚾ TRL日程管理")

# サイドバーに配置する場合のコード例
with st.sidebar:
    st.subheader("アプリ設定")
    if st.button("🔄 データを最新に更新"):
        st.cache_data.clear()
        st.rerun()
    st.info("※スプレッドシートの内容を反映させるにはこのボタンを押してください。")

# ==========================================
# 1. Googleスプレッドシートからの全7シート読込
# ==========================================
conn = st.connection("gsheets", type=GSheetsConnection)

def load_all_data():
    pool_df = conn.read(worksheet="match_pool", ttl=600)
    sched_df = conn.read(worksheet="schedule", ttl=600)
    res_df = conn.read(worksheet="results", ttl=600)
    teams_df = conn.read(worksheet="teams", ttl=600)
    grounds_df = conn.read(worksheet="grounds", ttl=600)
    ng_df = conn.read(worksheet="unavailable_days", ttl=600)
    slots_df = conn.read(worksheet="available_slots", ttl=600)
    return pool_df, sched_df, res_df, teams_df, grounds_df, ng_df, slots_df

pool_df, sched_df, res_df, teams_df, grounds_df, ng_df, slots_df = load_all_data()

# 【重要】読み込み直後に型の不一致を解消
if 'allow_far' in teams_df.columns:
    teams_df['allow_far'] = teams_df['allow_far'].fillna(False).astype(bool)
if 'is_far' in grounds_df.columns:
    grounds_df['is_far'] = grounds_df['is_far'].fillna(False).astype(bool)
if 'maps_url' in grounds_df.columns:
    grounds_df['maps_url'] = grounds_df['maps_url'].fillna("").astype(str)

# マスタデータの辞書化・リスト化
all_teams = teams_df['team'].tolist()
team_allow_far = dict(zip(teams_df['team'], teams_df['allow_far']))
team_leagues = dict(zip(teams_df['team'], teams_df['league'])) 
ground_options = grounds_df['name'].tolist()
ground_is_far = dict(zip(grounds_df['name'], grounds_df['is_far']))
ground_maps = dict(zip(grounds_df['name'], grounds_df['maps_url']))

# ==========================================
# 1.5 順位計算・総当たり表用ヘルパー関数
# ==========================================
def get_processed_matches(sched_df, res_df):
    processed = []
    res_dict = {}
    if not res_df.empty:
        res_dict = res_df.set_index('id').to_dict('index')
        
    for _, row in sched_df.iterrows():
        m_id = row['id']
        t1 = row['team1']
        t2 = row['team2']
        
        res = res_dict.get(m_id, {'status': '未消化', 'score': '-'})
        status = res.get('status', '未消化')
        score_str = str(res.get('score', '-'))
        
        match_info = {
            'id': m_id,
            'date': row.get('date', ''),
            'ground_name': row.get('ground_name', ''),
            'team1': t1,
            'team2': t2,
            'status': status,
            'score': score_str,
            'played': False,
            'score1': 0,
            'score2': 0,
            'winner': None,
            'loser': None,
            'is_draw': False
        }
        
        if status == '通常消化':
            try:
                if '-' in score_str:
                    s1_str, s2_str = score_str.split('-')
                    s1, s2 = int(s1_str), int(s2_str)
                    match_info['score1'] = s1
                    match_info['score2'] = s2
                    match_info['played'] = True
                    if s1 > s2:
                        match_info['winner'] = t1
                        match_info['loser'] = t2
                    elif s1 < s2:
                        match_info['winner'] = t2
                        match_info['loser'] = t1
                    else:
                        match_info['is_draw'] = True
            except Exception:
                pass
                
        elif status == '不戦敗':
            try:
                if '-' in score_str:
                    s1_str, s2_str = score_str.split('-')
                    s1, s2 = int(s1_str), int(s2_str)
                    match_info['score1'] = s1
                    match_info['score2'] = s2
                    match_info['played'] = True
                    if s1 > s2:
                        match_info['winner'] = t1
                        match_info['loser'] = t2
                    elif s1 < s2:
                        match_info['winner'] = t2
                        match_info['loser'] = t1
            except Exception:
                pass
                
        processed.append(match_info)
        
    return processed

def calculate_standings(teams, matches):
    stats = {}
    for team in teams:
        stats[team] = {
            'team': team,
            'played': 0,
            'wins': 0,
            'losses': 0,
            'draws': 0,
            'win_pct': 0.0,
            'goals_for': 0,
            'goals_against': 0,
            'goal_diff': 0,
        }
        
    for m in matches:
        if not m['played']:
            continue
        t1, t2 = m['team1'], m['team2']
        if t1 not in stats or t2 not in stats:
            continue
        
        stats[t1]['played'] += 1
        stats[t2]['played'] += 1
        
        s1, s2 = m['score1'], m['score2']
        stats[t1]['goals_for'] += s1
        stats[t1]['goals_against'] += s2
        stats[t2]['goals_for'] += s2
        stats[t2]['goals_against'] += s1
        
        if m['is_draw']:
            stats[t1]['draws'] += 1
            stats[t2]['draws'] += 1
        elif m['winner'] == t1:
            stats[t1]['wins'] += 1
            stats[t2]['losses'] += 1
        elif m['winner'] == t2:
            stats[t2]['wins'] += 1
            stats[t1]['losses'] += 1

    for team in teams:
        w = stats[team]['wins']
        l = stats[team]['losses']
        if w + l > 0:
            stats[team]['win_pct'] = w / (w + l)
        else:
            stats[team]['win_pct'] = 0.0
        stats[team]['goal_diff'] = stats[team]['goals_for'] - stats[team]['goals_against']

    # 一次ソート（勝率 -> 勝ち数 -> 得失点差）
    sorted_teams = sorted(
        teams,
        key=lambda t: (stats[t]['win_pct'], stats[t]['wins'], stats[t]['goal_diff']),
        reverse=True
    )
    
    # 同率グループの特定と直接対決（H2H）による決定
    def get_group_key(t):
        return (stats[t]['win_pct'], stats[t]['wins'], stats[t]['goal_diff'])
    
    from collections import defaultdict
    groups = defaultdict(list)
    for t in sorted_teams:
        groups[get_group_key(t)].append(t)
        
    final_sorted_teams = []
    sorted_group_keys = sorted(groups.keys(), reverse=True)
    for g_key in sorted_group_keys:
        group = groups[g_key]
        if len(group) == 1:
            final_sorted_teams.append(group[0])
        else:
            resolved_group = resolve_head_to_head(group, matches)
            final_sorted_teams.extend(resolved_group)
            
    standings = []
    for idx, team in enumerate(final_sorted_teams):
        t_stats = stats[team].copy()
        t_stats['rank'] = idx + 1
        standings.append(t_stats)
        
    return standings

def resolve_head_to_head(group_teams, matches):
    set_teams = set(group_teams)
    h2h_stats = {t: {'wins': 0, 'losses': 0, 'draws': 0, 'goals_for': 0, 'goals_against': 0, 'win_pct': 0.0, 'goal_diff': 0} for t in group_teams}
    
    for m in matches:
        if not m['played']:
            continue
        t1, t2 = m['team1'], m['team2']
        if t1 in set_teams and t2 in set_teams:
            s1, s2 = m['score1'], m['score2']
            h2h_stats[t1]['goals_for'] += s1
            h2h_stats[t1]['goals_against'] += s2
            h2h_stats[t2]['goals_for'] += s2
            h2h_stats[t2]['goals_against'] += s1
            
            if m['is_draw']:
                h2h_stats[t1]['draws'] += 1
                h2h_stats[t2]['draws'] += 1
            elif m['winner'] == t1:
                h2h_stats[t1]['wins'] += 1
                h2h_stats[t2]['losses'] += 1
            elif m['winner'] == t2:
                h2h_stats[t2]['wins'] += 1
                h2h_stats[t1]['losses'] += 1

    for t in group_teams:
        w = h2h_stats[t]['wins']
        l = h2h_stats[t]['losses']
        if w + l > 0:
            h2h_stats[t]['win_pct'] = w / (w + l)
        else:
            h2h_stats[t]['win_pct'] = 0.0
        h2h_stats[t]['goal_diff'] = h2h_stats[t]['goals_for'] - h2h_stats[t]['goals_against']
        
    sorted_group = sorted(
        group_teams,
        key=lambda t: (h2h_stats[t]['win_pct'], h2h_stats[t]['wins'], h2h_stats[t]['goal_diff']),
        reverse=True
    )
    return sorted_group

# ==========================================
# 2. 日程自動作成ロジック
# ==========================================
def make_monthly_schedule(match_list, slots, ng_days_dict, team_far_dict):
    # 通常グラウンド（is_far=False）を先に割り当てるため、is_far が False のスロットを先頭にするようにソート
    slots = sorted(slots, key=lambda s: s.get('is_far', False))

    allocated_matches = []
    team_monthly_counts = {team: 0 for team in all_teams}

    def get_teams_playing_on(date):
        playing = set()
        for am in allocated_matches:
            if am['date'] == date and am['match'] is not None:
                playing.add(am['match'][0])
                playing.add(am['match'][1])
        return playing

    def backtrack(slot_idx):
        if slot_idx == len(slots):
            return True
            
        current_slot = slots[slot_idx]
        date = current_slot['date']
        is_far_ground = current_slot['is_far']
        teams_playing_today = get_teams_playing_on(date)
        
        # 試行する対戦候補のインデックスリストを構築
        indices = []
        if not is_far_ground:
            # ①通常グラウンド: 遠方NGを含む試合を優先して試行
            for i, match in enumerate(match_list):
                t1, t2 = match
                is_far_match = team_far_dict.get(t1, False) and team_far_dict.get(t2, False)
                if not is_far_match:
                    indices.append(i)
            # ②通常グラウンド: 通常グラウンドが残っていたら、遠方OKのチーム同士の試合を試行
            for i, match in enumerate(match_list):
                t1, t2 = match
                is_far_match = team_far_dict.get(t1, False) and team_far_dict.get(t2, False)
                if is_far_match:
                    indices.append(i)
        else:
            # ③遠方グラウンド: 遠方OKのチーム同士の試合のみ試行
            for i, match in enumerate(match_list):
                t1, t2 = match
                is_far_match = team_far_dict.get(t1, False) and team_far_dict.get(t2, False)
                if is_far_match:
                    indices.append(i)
        
        for i in indices:
            match = match_list[i]
            t1, t2 = match
            
            # 各種制約チェック
            if team_monthly_counts[t1] >= 2 or team_monthly_counts[t2] >= 2:
                continue
            if t1 in teams_playing_today or t2 in teams_playing_today:
                continue
            if ng_days_dict.get(t1) == date or ng_days_dict.get(t2) == date:
                continue
            if is_far_ground and not (team_far_dict.get(t1, False) and team_far_dict.get(t2, False)):
                continue
                
            allocated_matches.append({
                'id': current_slot['id'], 
                'date': date,
                'slot': current_slot['slot'],
                'ground_name': current_slot['ground_name'],
                'match': match
            })
            team_monthly_counts[t1] += 1
            team_monthly_counts[t2] += 1
            match_list.pop(i)
            
            if backtrack(slot_idx + 1):
                return True
                
            match_list.insert(i, match)
            team_monthly_counts[t1] -= 1
            team_monthly_counts[t2] -= 1
            allocated_matches.pop()
            
        allocated_matches.append({
            'id': current_slot['id'],
            'date': date,
            'slot': current_slot['slot'],
            'ground_name': current_slot['ground_name'],
            'match': None
        })
        if backtrack(slot_idx + 1):
            return True
        allocated_matches.pop()
        return False

    pool_team_counts = Counter([team for match in match_list for team in match])
    random.shuffle(match_list)
    # 残り試合数が多いチームを優先するため、ペアの最大残り試合数を第一キー、合計を第二キーにして降順ソート
    match_list.sort(
        key=lambda m: (
            max(pool_team_counts[m[0]], pool_team_counts[m[1]]),
            pool_team_counts[m[0]] + pool_team_counts[m[1]]
        ),
        reverse=True
    )

    if backtrack(0):
        new_games = []
        filled_slot_ids = []
        for m in allocated_matches:
            if m['match'] is not None:
                new_games.append({
                    'id': m['id'],
                    'date': m['date'],
                    'slot': m['slot'],
                    'ground_name': m['ground_name'],
                    'team1': m['match'][0],
                    'team2': m['match'][1]
                })
                filled_slot_ids.append(m['id'])
        return pd.DataFrame(new_games), match_list, filled_slot_ids
    return pd.DataFrame(), match_list, []

# ==========================================
# 3. 画面UIレイアウト
# ==========================================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📅 NG日登録", 
    "🛠️ グラウンド枠・日程作成", 
    "🏆 試合結果入力", 
    "🏆 順位表・総当たり表",
    "📊 残試合数確認",
    "⚙️ マスタメンテ・データ確認"
])

# --- タブ1: NG日登録 ---
with tab1:
    st.header("📢 NG日登録")
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        select_team = st.selectbox("チーム名", ["選択してください"] + all_teams)
    with col_t2:
        today = datetime.now()
        if today.month == 12: next_month_first = datetime(today.year + 1, 1, 1)
        else: next_month_first = datetime(today.year, today.month + 1, 1)
        if next_month_first.month == 12: following_month_first = datetime(next_month_first.year + 1, 1, 1)
        else: following_month_first = datetime(next_month_first.year, next_month_first.month + 1, 1)
        
        next_month_sundays = []
        curr = next_month_first
        while curr < following_month_first:
            if curr.weekday() == 6: next_month_sundays.append(curr.date())
            curr += timedelta(days=1)
            
        select_ng_date = st.selectbox(
            "次月NG日", 
            options=next_month_sundays,
            format_func=lambda x: x.strftime('%Y-%m-%d（日）')
        )
        target_month = str(select_ng_date)[:7]
        st.caption(f"対象月（自動取得）: **{target_month}**")
        
    if select_team != "選択してください":
        if st.button("💾 NG日を登録・上書きする", type="primary"):
            new_ng_row = pd.DataFrame([{"team": select_team, "year_month": target_month, "ng_date": str(select_ng_date)}])
            filtered_ng = ng_df[~((ng_df['team'] == select_team) & (ng_df['year_month'] == target_month))]
            updated_ng_df = pd.concat([filtered_ng, new_ng_row], ignore_index=True)
            conn.update(worksheet="unavailable_days", data=updated_ng_df)
            st.cache_data.clear()
            st.success(f"【{select_team}】の {target_month} のNG日を保存（上書き）しました！")
            st.rerun()

    st.subheader("👀 NG日登録状況")
    st.dataframe(ng_df, width="stretch")

# --- タブ2: グラウンド枠登録 ＆ 日程自動作成 ---
with tab2:
    st.header("🛠️ グラウンド枠登録・日程作成")
    
    st.subheader("① 確保したグラウンド枠の登録（随時保存可能）")
    reg_mode = st.radio(
        "登録モードを選択してください", 
        ["1件ずつ登録", "画面でまとめて登録 (Excel風)", "テキストを直接貼り付けて登録", "CSVファイルから一括アップロード"], 
        horizontal=True
    )
    
    if reg_mode == "1件ずつ登録":
        with st.form("slot_input_form"):
            col_g1, col_g2, col_g3 = st.columns(3)
            slot_date = col_g1.date_input("確保した日付")
            slot_time = col_g2.selectbox("時間枠", [
                "9:00-12:00", "11:00-14:00", "12:00-15:00", "12:00-18:00", "13:00-17:00", "14:00-17:00", "15:00-18:00"
            ])
            slot_ground = col_g3.selectbox("グラウンド名", ground_options)
            
            submit_slot = st.form_submit_button("💾 この枠をデータベースに保存する")
            if submit_slot:
                generated_id = f"S_{slot_date}_{slot_time}_{slot_ground}"
                ym = str(slot_date)[:7]
                
                new_slot_row = pd.DataFrame([{
                    "id": generated_id, "date": str(slot_date), "slot": slot_time,
                    "ground_name": slot_ground, "year_month": ym, "status": "未割り当て"
                }])
                filtered_slots = slots_df[slots_df['id'] != generated_id]
                updated_slots_df = pd.concat([filtered_slots, new_slot_row], ignore_index=True)
                
                conn.update(worksheet="available_slots", data=updated_slots_df)
                st.cache_data.clear()
                st.success(f"枠 {generated_id} を「未割り当て」として保存しました！")
                st.rerun()

    elif reg_mode == "画面でまとめて登録 (Excel風)":
        st.markdown("💡 **Excel等から複数行をコピー（Ctrl+C）し、下の表に貼り付け（Ctrl+V）が可能です。**")
        input_template = pd.DataFrame(columns=["date", "slot", "ground_name"])
        edited_df = st.data_editor(
            input_template, 
            num_rows="dynamic", 
            width="stretch",
            column_config={
                "date": st.column_config.DateColumn("確保した日付", required=True),
                "slot": st.column_config.SelectboxColumn("時間枠", options=[
                    "9:00-12:00", "11:00-14:00", "12:00-15:00", "12:00-18:00", "13:00-17:00", "14:00-17:00", "15:00-18:00"
                ], required=True),
                "ground_name": st.column_config.SelectboxColumn("グラウンド名", options=ground_options, required=True)
            }
        )
        
        if st.button("💾 入力した枠をまとめてデータベースに保存する", type="primary", key="bulk_save_editor"):
            valid_df = edited_df.dropna(subset=["date", "slot", "ground_name"]).copy()
            if valid_df.empty:
                st.warning("登録する有効なデータが入力されていません。")
            else:
                valid_df['date'] = valid_df['date'].astype(str)
                valid_df['id'] = "S_" + valid_df['date'] + "_" + valid_df['slot'] + "_" + valid_df['ground_name']
                valid_df['year_month'] = valid_df['date'].str[:7]
                valid_df['status'] = "未割り当て"
                
                new_ids = valid_df['id'].tolist()
                filtered_slots = slots_df[~slots_df['id'].isin(new_ids)]
                updated_slots_df = pd.concat([filtered_slots, valid_df], ignore_index=True)
                
                conn.update(worksheet="available_slots", data=updated_slots_df)
                st.cache_data.clear()
                st.success(f"🎉 {len(valid_df)} 件のグラウンド枠をまとめて保存しました！")
                st.rerun()

    elif reg_mode == "テキストを直接貼り付けて登録":
        st.markdown("💡 **カンマ区切りのテキストをそのまま貼り付けて一括登録できます。**")
        default_example = "日,時間,施設名\n2026/07/05,12:00-15:00,額田G\n2026/07/05,15:00-18:00,額田G\n2026/07/12,12:00-15:00,三百田公園G"
        
        bulk_text = st.text_area(
            "ここにデータを貼り付けてください（1行目は「日,時間,施設名」にしてください）",
            value=default_example,
            height=200
        )
        
        if st.button("🚀 貼り付けたテキストから一括登録を実行", type="primary", key="bulk_save_text"):
            if not bulk_text.strip() or bulk_text.strip() == "日,時間,施設名":
                st.warning("登録するデータが入力されていません。")
            else:
                try:
                    parsed_df = pd.read_csv(io.StringIO(bulk_text.strip()))
                    expected_cols = ["日", "時間", "施設名"]
                    if not all(col in parsed_df.columns for col in expected_cols):
                        st.error("❌ 1行目のヘッダー（列名）は必ず「日,時間,施設名」にしてください。")
                    else:
                        parsed_df = parsed_df.rename(columns={"日": "date", "時間": "slot", "施設名": "ground_name"})
                        valid_df = parsed_df.dropna(subset=["date", "slot", "ground_name"]).copy()
                        
                        valid_df['date'] = pd.to_datetime(valid_df['date']).dt.strftime('%Y-%m-%d')
                        valid_df['id'] = "S_" + valid_df['date'] + "_" + valid_df['slot'] + "_" + valid_df['ground_name']
                        valid_df['year_month'] = valid_df['date'].str[:7]
                        valid_df['status'] = "未割り当て"
                        
                        invalid_grounds = valid_df[~valid_df['ground_name'].isin(ground_options)]['ground_name'].unique()
                        if len(invalid_grounds) > 0:
                            st.warning(f"⚠️ 注意: 「{', '.join(invalid_grounds)}」はグラウンドマスタに登録されていない名称です。自動作成に影響する可能性があるためご確認ください。")
                        
                        new_ids = valid_df['id'].tolist()
                        filtered_slots = slots_df[~slots_df['id'].isin(new_ids)]
                        updated_slots_df = pd.concat([filtered_slots, valid_df], ignore_index=True)
                        
                        conn.update(worksheet="available_slots", data=updated_slots_df)
                        st.cache_data.clear()
                        st.success(f"🎉 テキストから {len(valid_df)} 件のグラウンド枠を正常に登録しました！")
                        st.rerun()
                except Exception as e:
                    st.error(f"🚨 テキストの解析中にエラーが発生しました。エラー詳細: {e}")

    elif reg_mode == "CSVファイルから一括アップロード":
        st.markdown("💡 **以下のヘッダー（列名）を持つCSVファイルをアップロードしてください。**")
        st.code("date,slot,ground_name\n2026-07-12,13:00-17:00,南明柄グラウンド")
        
        uploaded_file = st.file_uploader("CSVファイルを選択", type=["csv"])
        if uploaded_file is not None:
            try:
                uploaded_df = pd.read_csv(uploaded_file)
                required_cols = ["date", "slot", "ground_name"]
                
                if not all(col in uploaded_df.columns for col in required_cols):
                    st.error(f"❌ CSVファイルのヘッダーが正しくありません。 {required_cols} を含めてください。")
                else:
                    st.write("📋 アップロードデータのプレビュー:")
                    st.dataframe(uploaded_df, width="stretch")
                    
                    if st.button("💾 CSVのデータをデータベースに保存する", type="primary", key="bulk_save_csv"):
                        valid_df = uploaded_df.dropna(subset=["date", "slot", "ground_name"]).copy()
                        valid_df['date'] = valid_df['date'].astype(str)
                        valid_df['id'] = "S_" + valid_df['date'] + "_" + valid_df['slot'] + "_" + valid_df['ground_name']
                        valid_df['year_month'] = valid_df['date'].str[:7]
                        valid_df['status'] = "未割り当て"
                        
                        new_ids = valid_df['id'].tolist()
                        filtered_slots = slots_df[~slots_df['id'].isin(new_ids)]
                        updated_slots_df = pd.concat([filtered_slots, valid_df], ignore_index=True)
                        
                        conn.update(worksheet="available_slots", data=updated_slots_df)
                        st.cache_data.clear()
                        st.success(f"🎉 CSVから {len(valid_df)} 件のグラウンド枠をまとめて保存しました！")
                        st.rerun()
            except Exception as e:
                st.error(f"🚨 ファイルの読み込み中にエラーが発生しました: {e}")

    st.markdown("---")
    
    # 2-2. 保存された枠を使った日程の自動作成
    st.subheader("② 次月の日程自動作成の実行")
    target_month_sched = st.selectbox("作成する対象月を選択", ["2026-07", "2026-08", "2026-09"], index=1, key="sb_month")
    
    current_month_slots = slots_df[(slots_df['year_month'] == target_month_sched) & (slots_df['status'] == "未割り当て")].copy()
    
    st.write(f"📊 現在スプレッドシートに保存されている **{target_month_sched} 分の未割り当て枠**: {len(current_month_slots)} 件")
    st.dataframe(current_month_slots, width="stretch")
    
    if st.button("🔥 保存された枠を元に日程を自動生成する", type="primary"):
        if current_month_slots.empty:
            st.error("自動生成に使用できる「未割り当て」のグラウンド枠がありません。先に上のフォームから枠を登録・保存してください。")
        else:
            current_month_slots['is_far'] = current_month_slots['ground_name'].map(ground_is_far)
            slots_input_list = current_month_slots.to_dict('records')
            
            current_pool = list(zip(pool_df['team1'], pool_df['team2']))
            monthly_ng_df = ng_df[ng_df['year_month'] == target_month_sched]
            ng_days_dict = dict(zip(monthly_ng_df['team'], monthly_ng_df['ng_date']))
            
            new_sched_df, rem_pool_list, filled_slot_ids = make_monthly_schedule(
                current_pool, slots_input_list, ng_days_dict, team_allow_far
            )
            
            if not new_sched_df.empty:
                updated_sched_df = pd.concat([sched_df, new_sched_df], ignore_index=True)
                conn.update(worksheet="schedule", data=updated_sched_df)
                
                updated_pool_df = pd.DataFrame(rem_pool_list, columns=['team1', 'team2'])
                conn.update(worksheet="match_pool", data=updated_pool_df)
                
                slots_df.loc[slots_df['id'].isin(filled_slot_ids), 'status'] = '割り当て済み'
                conn.update(worksheet="available_slots", data=slots_df)
                st.cache_data.clear()
                
                st.success("🎉 日程の自動生成およびグラウンド枠のステータス更新が完了しました！")
                st.rerun()
            else:
                st.error("❌ 条件（各チームのNG日や遠方制限など）が厳しく、マッチングする組み合わせが見つかりませんでした。枠を増やすか調整してください。")

    # 選択した月の作成済み日程を取得
    target_month_scheduled = sched_df[sched_df['date'].astype(str).str.startswith(target_month_sched)] if not sched_df.empty else pd.DataFrame()
    
    if not target_month_scheduled.empty:
        st.markdown("---")
        st.subheader("⚠️ 作成済み日程の削除・やり直し")
        st.warning(f"現在、**{target_month_sched}** の作成済み試合が **{len(target_month_scheduled)}件** 登録されています。作成し直す場合は、一旦削除してください。")
        st.dataframe(target_month_scheduled[['date', 'slot', 'ground_name', 'team1', 'team2']], width="stretch", hide_index=True)
        
        confirm_delete = st.checkbox("上記日程を削除し、試合ペアを対戦プールに戻し、グラウンド枠を「未割り当て」に戻すことに同意します。", key="confirm_delete_month")
        if st.button("🗑️ この月の日程を削除してやり直す", type="primary", disabled=not confirm_delete):
            with st.spinner("削除処理を実行中..."):
                # 1. 試合をプールに戻す
                returned_matches = target_month_scheduled[['team1', 'team2']].copy()
                updated_pool_df = pd.concat([pool_df, returned_matches], ignore_index=True)
                conn.update(worksheet="match_pool", data=updated_pool_df)
                
                # 2. グラウンドを未割り当てにする
                deleted_ids = target_month_scheduled['id'].tolist()
                slots_df.loc[slots_df['id'].isin(deleted_ids), 'status'] = '未割り当て'
                conn.update(worksheet="available_slots", data=slots_df)
                
                # 3. スケジュールから削除
                updated_sched_df = sched_df[~sched_df['id'].isin(deleted_ids)]
                conn.update(worksheet="schedule", data=updated_sched_df)
                
                # 4. 結果データからも削除 (不整合防止)
                if not res_df.empty:
                    updated_res_df = res_df[~res_df['id'].isin(deleted_ids)]
                    conn.update(worksheet="results", data=updated_res_df)
                
                # キャッシュクリアと画面リロード
                st.cache_data.clear()
                st.success(f"🎉 {target_month_sched} の日程 {len(target_month_scheduled)}件 を削除し、プールおよび空き枠を元に戻しました！")
                st.rerun()

    if not sched_df.empty:
        st.subheader("🗓️ 確定スケジュール一覧")
        display_sched = sched_df.copy()
        display_sched['GoogleMap_URL'] = display_sched['ground_name'].map(ground_maps)
        st.dataframe(display_sched, width="stretch")

        # 📱 LINEグループ送信用のテキスト作成
        st.markdown("---")
        st.subheader("📱 LINEグループ配信用テキスト生成")
        
        line_sched = sched_df[sched_df['date'].astype(str).str.startswith(target_month_sched)].copy()
        
        if line_sched.empty:
            st.info(f"💡 選択中の対象月（{target_month_sched}）の確定スケジュールがまだ登録されていません。")
        else:
            display_month = str(int(target_month_sched.split("-")[1]))
            line_sched = line_sched.sort_values(by=["date", "slot"])
            
            line_msg = f"【{display_month}月日程】\n"
            line_msg += "日程担当の中山です。\n"
            line_msg += f"{display_month}月の日程連絡させていただきます。\n"
            line_msg += "※左に記載のチームがホームチームです\n\n"
            
            for _, row in line_sched.iterrows():
                try:
                    date_formatted = datetime.strptime(row['date'], "%Y-%m-%d").strftime("%m/%d")
                except Exception:
                    date_formatted = row['date'][5:10].replace('-', '/')
                
                line_msg += f"{row['team1']}-{row['team2']}\n"
                line_msg += f"{date_formatted} {row['slot']} {row['ground_name']}\n\n"
            
            # 未割り当てグラウンドを取得して追加
            vacant_slots = pd.DataFrame()
            if not slots_df.empty:
                vacant_slots = slots_df[(slots_df['year_month'] == target_month_sched) & (slots_df['status'] == "未割り当て")].copy()
            
            if not vacant_slots.empty:
                vacant_slots = vacant_slots.sort_values(by=["date", "slot"])
                line_msg += "空きグラウンド\n"
                for _, row in vacant_slots.iterrows():
                    try:
                        v_date_formatted = datetime.strptime(row['date'], "%Y-%m-%d").strftime("%m/%d")
                    except Exception:
                        v_date_formatted = row['date'][5:10].replace('-', '/')
                    line_msg += f"{v_date_formatted} {row['slot']} {row['ground_name']}\n"
                line_msg += "\n"
            
            line_msg = line_msg.strip()
            st.text_area(
                "📋 以下のテキストエリアをクリックし、全選択（Ctrl+A / ⌘+A）してコピーしてください", 
                value=line_msg, 
                height=350
            )

# ==========================================
# 4. 試合結果入力（タブ3）
# ==========================================
with tab3:
    st.header("🏆 試合結果入力")
    if sched_df.empty:
        st.warning("確定した日程がありません。")
    else:
        for idx, row in sched_df.iterrows():
            m_id = row['id']
            existing_res = res_df[res_df['id'] == m_id]
            current_status = existing_res['status'].values[0] if not existing_res.empty else "未消化"
            current_score = existing_res['score'].values[0] if not existing_res.empty else "-"
            
            status_display_text = f"現在のステータス: {current_status}"
            
            if current_status == "通常消化":
                status_emoji = "🟢"
                bg_color = "#e6f4ea"
                text_color = "#137333"
                try:
                    if "-" in str(current_score):
                        s1_str, s2_str = str(current_score).split("-")
                        s1, s2 = int(s1_str), int(s2_str)
                        if s1 > s2:
                            status_display_text += f" (⭕ 勝者: {row['team1']} [{s1}] / ❌ 敗者: {row['team2']} [{s2}])"
                        elif s2 > s1:
                            status_display_text += f" (❌ 敗者: {row['team1']} [{s1}] / ⭕ 勝者: {row['team2']} [{s2}])"
                        else:
                            status_display_text += f" (🔺 引き分け: {row['team1']} [{s1}] - [{s2}] {row['team2']})"
                    else:
                        status_display_text += f" (スコア: {current_score})"
                except Exception:
                    status_display_text += f" (スコア: {current_score})"
                
            elif current_status == "雨天中止":
                status_emoji = "⚫"
                bg_color = "#f1f3f4"
                text_color = "#5f6368"
                
            elif current_status == "不戦敗":
                status_emoji = "🟡"
                bg_color = "#fef7e0"
                text_color = "#b06000"
                if current_score == "0-10":
                    status_display_text += f" (❌ 不戦敗: {row['team1']} / ⭕ 不戦勝: {row['team2']})"
                elif current_score == "10-0":
                    status_display_text += f" (⭕ 不戦勝: {row['team1']} / ❌ 不戦敗: {row['team2']})"
                else:
                    status_display_text += f" (スコア: {current_score})"
            else:
                status_emoji = "⚪"
                bg_color = "#e8f0fe"
                text_color = "#1a73e8"
            
            expander_title = f"{status_emoji} 【{row['date']} {row['slot']} @{row['ground_name']}】 {row['team1']} vs {row['team2']} (現在の状態: {current_status})"
            
            with st.expander(expander_title):
                status_bar_html = f"""
                <div style="background-color: {bg_color}; color: {text_color}; padding: 10px 12px; border-radius: 5px; margin-bottom: 15px; font-weight: bold; border-left: 6px solid {text_color};">
                    {status_emoji} {status_display_text}
                </div>
                """
                st.markdown(status_bar_html, unsafe_allow_html=True)
                
                status = st.selectbox("試合ステータスを更新する", ["未消化", "通常消化", "雨天中止", "不戦敗"], key=f"st_{m_id}")
                
                if status == "未消化":
                    if st.button("結果を保存", key=f"save_unplayed_{m_id}"):
                        if current_status == "雨天中止":
                            updated_pool_df = pool_df[~((pool_df['team1'] == row['team1']) & (pool_df['team2'] == row['team2']))]
                            conn.update(worksheet="match_pool", data=updated_pool_df)
                        
                        if current_status in ["通常消化", "不戦敗", "雨天中止"]:
                            updated_res_df = res_df[res_df['id'] != m_id]
                        else:
                            new_res = pd.DataFrame([{"id": m_id, "status": "未消化", "score": "-"}])
                            updated_res_df = pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True)
                        
                        conn.update(worksheet="results", data=updated_res_df)
                        st.cache_data.clear()
                        st.success("ステータスを『未消化』に更新しました。")
                        st.rerun()

                elif status == "通常消化":
                    sc1 = st.number_input(f"{row['team1']} スコア", min_value=0, value=0, key=f"sc1_{m_id}")
                    sc2 = st.number_input(f"{row['team2']} スコア", min_value=0, value=0, key=f"sc2_{m_id}")
                    if st.button("結果を保存", key=f"save_{m_id}"):
                        if current_status == "雨天中止":
                            updated_pool_df = pool_df[~((pool_df['team1'] == row['team1']) & (pool_df['team2'] == row['team2']))]
                            conn.update(worksheet="match_pool", data=updated_pool_df)
                            
                        new_res = pd.DataFrame([{"id": m_id, "status": "通常消化", "score": f"{sc1}-{sc2}"}])
                        updated_res_df = pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True)
                        conn.update(worksheet="results", data=updated_res_df)
                        st.cache_data.clear()
                        st.success("試合結果を保存しました。")
                        st.rerun()
                        
                elif status == "雨天中止":
                    if st.button("🚨 雨天中止を確定して再試合プールへ戻す", key=f"rain_{m_id}"):
                        if current_status != "雨天中止":
                            canceled_match = pd.DataFrame([{"team1": row['team1'], "team2": row['team2']}])
                            updated_pool_df = pd.concat([canceled_match, pool_df], ignore_index=True)
                            conn.update(worksheet="match_pool", data=updated_pool_df)
                        
                        new_res = pd.DataFrame([{"id": m_id, "status": "雨天中止", "score": ""}])
                        updated_res_df = pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True)
                        
                        conn.update(worksheet="results", data=updated_res_df)
                        st.cache_data.clear()
                        st.success("スケジュールを削除し、対戦カードをプールに戻しました。")
                        st.rerun()

                elif status == "不戦敗":
                    lose_team = st.radio(
                        "どちらのチームが不戦敗（負け）となりましたか？", 
                        [row['team1'], row['team2']], 
                        key=f"lose_{m_id}"
                    )
                    
                    if st.button("結果を保存", key=f"save_forfeit_{m_id}"):
                        if current_status == "雨天中止":
                            updated_pool_df = pool_df[~((pool_df['team1'] == row['team1']) & (pool_df['team2'] == row['team2']))]
                            conn.update(worksheet="match_pool", data=updated_pool_df)
                            
                        if lose_team == row['team1']:
                            final_score = "0-10"
                        else:
                            final_score = "10-0"
                            
                        new_res = pd.DataFrame([{"id": m_id, "status": "不戦敗", "score": final_score}])
                        updated_res_df = pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True)
                        conn.update(worksheet="results", data=updated_res_df)
                        st.cache_data.clear()
                        st.success(f"不戦敗の結果を保存しました。（スコア: {final_score}）")
                        st.rerun()

# --- タブ4: 順位表・総当たり表 ---
with tab4:
    st.header("🏆 順位表・総当たり表")
    
    # 試合結果データのパース
    processed_matches = get_processed_matches(sched_df, res_df)
    
    # 【共通処理】全チームの総合順位を一度だけ計算
    all_teams_list = teams_df['team'].tolist()
    standings_all = calculate_standings(all_teams_list, processed_matches)
    standings_all_df = pd.DataFrame(standings_all)
    standings_all_df['リーグ'] = standings_all_df['team'].map(team_leagues)
    
    # 表示用カラム名へのリネーム
    display_cols = {
        'rank': '順位', 'team': 'チーム名', 'played': '試合数',
        'wins': '勝', 'losses': '敗', 'draws': '分',
        'win_pct': '勝率', 'goals_for': '得点', 
        'goals_against': '失点', 'goal_diff': '得失点差', 'リーグ': 'リーグ'
    }
    standings_display_df = standings_all_df.rename(columns=display_cols)
    
    # サブタブ作成
    sub_tab1, sub_tab2, sub_tab3 = st.tabs([
        "🏆 リーグ別順位",
        "🌎 総合順位",
        "📊 総当たり表 (マトリックス)"
    ])
    
    with sub_tab1:
        st.subheader("🏆 リーグ別順位表")
        leagues = sorted(list(teams_df['league'].dropna().unique()))
        selected_league = st.selectbox("表示するリーグを選択してください", leagues, key="select_league")
        
        # 選択されたリーグで絞り込み
        league_df = standings_display_df[standings_display_df['リーグ'] == selected_league].copy()
        
        # 順位をフィルタリング後のデータに合わせて再採番
        league_df = league_df.sort_values(by=['勝率', '勝', '得失点差'], ascending=False)
        league_df['順位'] = range(1, len(league_df) + 1)
        
        st.dataframe(
            league_df[['順位', 'チーム名', '試合数', '勝', '敗', '分', '勝率', '得点', '失点', '得失点差']],
            width="stretch", hide_index=True
        )
        # 【追加】順位決定ルールのメモ
        st.markdown("""
        <div style="background-color: #f8f9fa; padding: 10px; border-radius: 5px; font-size: 0.85em; color: #5f6368;">
        <strong>【順位決定ルール】</strong><br>
        1. 勝率 ＞ 2. 勝ち数 ＞ 3. 得失点差 ＞ 4. 直接対決
        </div>
        """, unsafe_allow_html=True)

    with sub_tab2:
        st.subheader("🌎 総合順位表")
        st.dataframe(
            standings_display_df[['順位', 'チーム名', 'リーグ', '試合数', '勝', '敗', '分', '勝率', '得点', '失点', '得失点差']],
            width="stretch", hide_index=True
        )
        # 【追加】順位決定ルールのメモ
        st.markdown("""
        <div style="background-color: #f8f9fa; padding: 10px; border-radius: 5px; font-size: 0.85em; color: #5f6368;">
        <strong>【順位決定ルール】</strong><br>
        1. 勝率 ＞ 2. 勝ち数 ＞ 3. 得失点差 ＞ 4. 直接対決
        </div>
        """, unsafe_allow_html=True)

    with sub_tab3:
        st.subheader("📊 全試合対戦総当たり表 (マトリックス)")
        st.markdown("""
        - **行が「ホームチーム（後攻）」**、**列が「アウェイチーム（先攻）」**となります。
        - リーグ内対戦（総当たり2回）は2箇所、インターリーグ（総当たり1回）は設定された1箇所のみ表示されます。
        """)
        
        sorted_teams_by_league = teams_df.sort_values(by=['league', 'team'])['team'].tolist()
        
        pool_matches = set()
        if not pool_df.empty:
            for _, row in pool_df.iterrows():
                pool_matches.add((row['team1'], row['team2']))
                
        match_map = {}
        for m in processed_matches:
            match_map[(m['team1'], m['team2'])] = m
            
        html = """
        <style>
        .matrix-container {
            overflow-x: auto;
            margin: 15px 0;
            padding: 5px;
        }
        .matrix-table {
            width: 100%;
            border-collapse: collapse;
            font-family: 'Outfit', 'Inter', 'Helvetica Neue', sans-serif;
            font-size: 14px;
            text-align: center;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        }
        .matrix-table th, .matrix-table td {
            border: 1px solid #e0e0e0;
            padding: 10px 8px;
            min-width: 90px;
            vertical-align: middle;
        }
        .matrix-table th {
            background-color: #f8f9fa;
            color: #3c4043;
            font-weight: 600;
        }
        .matrix-table th.header-team-a {
            background-color: #e8f0fe !important;
            color: #1a73e8 !important;
            font-weight: bold;
        }
        .matrix-table th.header-team-b {
            background-color: #fef7e0 !important;
            color: #b06000 !important;
            font-weight: bold;
        }
        .matrix-table th.corner {
            background-color: #f1f3f4;
            font-size: 11px;
            color: #5f6368;
            width: 110px;
        }
        .cell-diagonal {
            background-color: #e8eaed !important;
            color: #9aa0a6;
            font-size: 16px;
        }
        .cell-none {
            background-color: #f1f3f4 !important;
            color: #bdc1c6;
            font-size: 12px;
        }
        .cell-win {
            background-color: #e6f4ea !important;
            color: #137333 !important;
            font-weight: bold;
        }
        .cell-loss {
            background-color: #fce8e6 !important;
            color: #c5221f !important;
        }
        .cell-draw {
            background-color: #fef7e0 !important;
            color: #b06000 !important;
            font-weight: bold;
        }
        .cell-unplayed {
            background-color: #e8f0fe !important;
            color: #1a73e8 !important;
            font-size: 11px;
            line-height: 1.2;
        }
        .cell-info {
            display: block;
            font-size: 11px;
            margin-top: 3px;
            font-weight: normal;
            opacity: 0.85;
        }
        .badge-league {
            display: inline-block;
            padding: 2px 6px;
            font-size: 10px;
            border-radius: 4px;
            margin-left: 5px;
            font-weight: bold;
        }
        .badge-a {
            background-color: #e8f0fe;
            color: #1a73e8;
            border: 1px solid #1a73e8;
        }
        .badge-b {
            background-color: #fef7e0;
            color: #b06000;
            border: 1px solid #b06000;
        }
        </style>
        <div class="matrix-container">
        <table class="matrix-table">
          <thead>
            <tr>
              <th class="corner">H \\ A<br>(ホーム＼アウェイ)</th>
        """
        
        for t in sorted_teams_by_league:
            l = team_leagues.get(t, '')
            header_class = 'header-team-a' if l == 'A' else 'header-team-b'
            badge_class = 'badge-a' if l == 'A' else 'badge-b'
            html += f'<th class="{header_class}">{t}<br><span class="badge-league {badge_class}">L-{l}</span></th>'
            
        html += """
            </tr>
          </thead>
          <tbody>
        """
        
        for t1 in sorted_teams_by_league:
            l1 = team_leagues.get(t1, '')
            header_class1 = 'header-team-a' if l1 == 'A' else 'header-team-b'
            badge_class1 = 'badge-a' if l1 == 'A' else 'badge-b'
            html += f'<tr><th class="{header_class1}" style="text-align: left; padding-left: 12px;">{t1} <span class="badge-league {badge_class1}">L-{l1}</span></th>'
            
            for t2 in sorted_teams_by_league:
                if t1 == t2:
                    html += '<td class="cell-diagonal">＼</td>'
                else:
                    match = match_map.get((t1, t2))
                    if match is None:
                        if (t1, t2) in pool_matches:
                            html += '<td class="cell-none">-</td>'
                        else:
                            html += '<td class="cell-diagonal"></td>'
                    else:
                        status = match['status']
                        score_str = match['score']
                        
                        if status == '通常消化':
                            s1, s2 = match['score1'], match['score2']
                            if s1 > s2:
                                html += f'<td class="cell-win">○<span class="cell-info">{s1}-{s2}</span></td>'
                            elif s1 < s2:
                                html += f'<td class="cell-loss">×<span class="cell-info">{s1}-{s2}</span></td>'
                            else:
                                html += f'<td class="cell-draw">△<span class="cell-info">{s1}-{s2}</span></td>'
                                
                        elif status == '不戦敗':
                            s1, s2 = match['score1'], match['score2']
                            if s1 > s2:
                                html += '<td class="cell-win">○<span class="cell-info">不戦勝</span></td>'
                            else:
                                html += '<td class="cell-loss">×<span class="cell-info">不戦敗</span></td>'
                                
                        elif status == '雨天中止':
                            html += '<td class="cell-none">中止</td>'
                            
                        else:
                            date_val = match.get('date', '')
                            try:
                                formatted_date = datetime.strptime(date_val, "%Y-%m-%d").strftime("%m/%d")
                            except Exception:
                                formatted_date = date_val[5:10].replace('-', '/') if len(date_val) >= 10 else date_val
                                
                            g_name = match.get('ground_name', '')
                            html += f'<td class="cell-unplayed">未<span class="cell-info">{formatted_date}<br>{g_name}</span></td>'
            html += '</tr>'
            
        html += """
          </tbody>
        </table>
        </div>
        """
        
        st.markdown(html, unsafe_allow_html=True)

# --- タブ5: 残試合数確認 ---
with tab5:
    st.header("📊 残試合数確認")
    st.subheader("🔥 未消化試合")
    st.dataframe(pool_df, width="stretch")
    
    st.markdown("リーグ全体の残り試合数の集計状況です。")
    today_str = datetime.now().strftime('%Y-%m-%d')
    remaining_data = []
    
    if not res_df.empty and 'id' in res_df.columns:
        exclude_ids = set(res_df[res_df['status'].isin(['通常消化', '不戦敗', '雨天中止'])]['id'].astype(str).tolist())
    else:
        exclude_ids = set()

    for team in all_teams:
        unallocated = ((pool_df['team1'] == team) | (pool_df['team2'] == team)).sum() if not pool_df.empty else 0
        past_unplayed = 0
        future_unplayed = 0
        if not sched_df.empty:
            unplayed_sched = sched_df[~sched_df['id'].astype(str).isin(exclude_ids)]
            team_sched = unplayed_sched[(unplayed_sched['team1'] == team) | (unplayed_sched['team2'] == team)]
            if not team_sched.empty:
                past_unplayed = (team_sched['date'].astype(str) < today_str).sum()
                future_unplayed = (team_sched['date'].astype(str) >= today_str).sum()
        remaining_data.append({
            "チーム名": team,
            "総残試合数": unallocated + past_unplayed + future_unplayed,
            "未日程 (プール内)": unallocated,
            "日程済 (過去の未消化)": past_unplayed,
            "日程済 (未来の未消化)": future_unplayed
        })
    remaining_df = pd.DataFrame(remaining_data).sort_values(by="総残試合数", ascending=False)
    st.dataframe(remaining_df, width="stretch", hide_index=True)

# --- タブ6: マスタメンテナンス ---
with tab6:
    st.header("⚙️ マスタメンテ・データ確認")
    m_tab1, m_tab2, m_tab3 = st.tabs(["🏟️ グラウンドマスタ編集", "🏃 チームマスタ編集", "📋 確保枠確認"])
    
    with m_tab1:
        # 型の再キャスト（エラー回避）
        grounds_df["is_far"] = grounds_df["is_far"].fillna(False).astype(bool)
        grounds_df["maps_url"] = grounds_df["maps_url"].fillna("").astype(str)
        
        edited_grounds_df = st.data_editor(grounds_df, num_rows="dynamic", width="stretch", key="master_grounds_editor", column_config={
            "name": st.column_config.TextColumn("グラウンド名", required=True),
            "is_far": st.column_config.CheckboxColumn("遠方フラグ"),
            "maps_url": st.column_config.TextColumn("GoogleMap URL")
        })
        if st.button("💾 グラウンドマスタを保存"):
            conn.update(worksheet="grounds", data=edited_grounds_df.fillna(""))
            st.rerun()
            
    with m_tab2:
        # 型の再キャスト（エラー回避）
        teams_df["allow_far"] = teams_df["allow_far"].fillna(False).astype(bool)
        
        edited_teams_df = st.data_editor(teams_df, num_rows="dynamic", width="stretch", key="master_teams_editor", column_config={
            "team": st.column_config.TextColumn("チーム名", required=True),
            "allow_far": st.column_config.CheckboxColumn("遠方対応可否")
        })
        if st.button("💾 チームマスタを保存"):
            conn.update(worksheet="teams", data=edited_teams_df.fillna(""))
            st.rerun()

    with m_tab3:
        st.subheader("📋 確保グラウンド枠の全履歴")
        st.dataframe(slots_df, width="stretch")
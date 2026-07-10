import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import random
from datetime import datetime

st.set_page_config(page_title="野球リーグ総合管理システム", layout="wide")
st.title("⚾ 野球リーグ日程・マスタ完全管理システム")

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

# マスタデータの辞書化・リスト化
all_teams = teams_df['team'].tolist()
team_allow_far = dict(zip(teams_df['team'], teams_df['allow_far']))
ground_options = grounds_df['name'].tolist()
ground_is_far = dict(zip(grounds_df['name'], grounds_df['is_far']))
ground_maps = dict(zip(grounds_df['name'], grounds_df['maps_url']))

# ==========================================
# 2. 日程自動作成ロジック（バックトラッキング）
# ==========================================
def make_monthly_schedule(match_list, slots, ng_days_dict, team_far_dict):
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
        
        for i, match in enumerate(match_list):
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
                'id': current_slot['id'], # グラウンド枠のIDを引き継ぐ
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
            
        # 試合が組めなかった場合は「空き枠」として処理
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

    random.shuffle(match_list)
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
                filled_slot_ids.append(m['id']) # マッチングが成功した枠のID
        return pd.DataFrame(new_games), match_list, filled_slot_ids
    return pd.DataFrame(), match_list, []

# ==========================================
# 3. 画面UIレイアウト（4つのタブ）
# ==========================================
tab1, tab2, tab3, tab4 = st.tabs([
    "📅 チーム代表者向け：NG日登録", 
    "🛠️ 運営向け：グラウンド枠登録・自動作成", 
    "🏆 試合結果入力", 
    "⚙️ マスタ・データ確認"
])

# --- タブ1: NG日登録 ---
with tab1:
    st.header("📢 各チーム代表者専用：次月のNG日登録フォーム")
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        select_team = st.selectbox("あなたのチーム名を選択してください", ["選択してください"] + all_teams)
    with col_t2:
        select_ng_date = st.date_input("試合NGにする日を選択")
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

    st.subheader("👀 現在の各チームのNG日登録状況")
    st.dataframe(ng_df, use_container_width=True)

# --- タブ2: グラウンド枠登録 ＆ 日程自動作成 ---
with tab2:
    st.header("🛠️ グラウンド枠の管理と日程の自動生成")
    
    # 2-1. グラウンド枠の一時保存フォーム
    st.subheader("① 確保したグラウンド枠の登録（随時保存可能）")
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

    st.markdown("---")
    
    # 2-2. 保存された枠を使った日程の自動作成
    st.subheader("② 次月の日程自動作成の実行")
    target_month_sched = st.selectbox("作成する対象月を選択", ["2026-07", "2026-08", "2026-09"], index=1, key="sb_month")
    
    current_month_slots = slots_df[(slots_df['year_month'] == target_month_sched) & (slots_df['status'] == "未割り当て")].copy()
    
    st.write(f"📊 現在スプレッドシートに保存されている **{target_month_sched} 分の未割り当て枠**: {len(current_month_slots)} 件")
    st.dataframe(current_month_slots, use_container_width=True)
    
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

    if not sched_df.empty:
        st.subheader("🗓️ 確定スケジュール一覧")
        display_sched = sched_df.copy()
        display_sched['GoogleMap_URL'] = display_sched['ground_name'].map(ground_maps)
        st.dataframe(display_sched, use_container_width=True)

# --- タブ3: 試合結果入力 ---
with tab3:
    st.header("🏆 試合結果の登録")
    if sched_df.empty:
        st.warning("確定した日程がありません。")
    else:
        for idx, row in sched_df.iterrows():
            m_id = row['id']
            existing_res = res_df[res_df['id'] == m_id]
            current_status = existing_res['status'].values[0] if not existing_res.empty else "未消化"
            current_score = existing_res['score'].values[0] if not existing_res.empty else "-"
            
            status_display_text = f"現在のステータス: {current_status}"
            
            # ステータスに応じた「絵文字」「背景色・文字色」および詳細可視化ロジック
            if current_status == "通常消化":
                status_emoji = "🟢"
                bg_color = "#e6f4ea"      # 黄緑
                text_color = "#137333"
                
                # 【追加改善】通常消化のスコアを解析して ○ × 判定を表示
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
                bg_color = "#f1f3f4"      # 灰色
                text_color = "#5f6368"
                
            elif current_status == "不戦敗":
                status_emoji = "🟡"
                bg_color = "#fef7e0"      # クリーム色
                text_color = "#b06000"
                if current_score == "0-10":
                    status_display_text += f" (❌ 不戦敗: {row['team1']} / ⭕ 不戦勝: {row['team2']})"
                elif current_score == "10-0":
                    status_display_text += f" (⭕ 不戦勝: {row['team1']} / ❌ 不戦敗: {row['team2']})"
                else:
                    status_display_text += f" (スコア: {current_score})"
            else:
                status_emoji = "⚪"
                bg_color = "#e8f0fe"      # 薄い青
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
                
                # 「未消化」への更新処理
                if status == "未消化":
                    if st.button("結果を保存", key=f"save_unplayed_{m_id}"):
                        if current_status in ["通常消化", "不戦敗"]:
                            updated_res_df = res_df[res_df['id'] != m_id]
                        else:
                            new_res = pd.DataFrame([{"id": m_id, "status": "未消化", "score": "-"}])
                            updated_res_df = pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True)
                        
                        conn.update(worksheet="results", data=updated_res_df)
                        st.cache_data.clear()
                        st.success("ステータスを『未消化』に更新しました（スコアレコードはリセットされました）。")
                        st.rerun()

                elif status == "通常消化":
                    sc1 = st.number_input(f"{row['team1']} スコア", min_value=0, value=0, key=f"sc1_{m_id}")
                    sc2 = st.number_input(f"{row['team2']} スコア", min_value=0, value=0, key=f"sc2_{m_id}")
                    if st.button("結果を保存", key=f"save_{m_id}"):
                        new_res = pd.DataFrame([{"id": m_id, "status": "通常消化", "score": f"{sc1}-{sc2}"}])
                        updated_res_df = pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True)
                        conn.update(worksheet="results", data=updated_res_df)
                        st.cache_data.clear()
                        st.success("試合結果を保存しました。")
                        st.rerun()
                        
                # 「雨天中止」への更新処理
                elif status == "雨天中止":
                    if st.button("🚨 雨天中止を確定して再試合プールへ戻す", key=f"rain_{m_id}"):
                        updated_sched_df = sched_df[sched_df['id'] != m_id]
                        conn.update(worksheet="schedule", data=updated_sched_df)
                        
                        canceled_match = pd.DataFrame([{"team1": row['team1'], "team2": row['team2']}])
                        updated_pool_df = pd.concat([canceled_match, pool_df], ignore_index=True)
                        conn.update(worksheet="match_pool", data=updated_pool_df)
                        
                        slots_df.loc[slots_df['id'] == m_id, 'status'] = '未割り当て'
                        conn.update(worksheet="available_slots", data=slots_df)
                        
                        if current_status in ["通常消化", "不戦敗"]:
                            updated_res_df = res_df[res_df['id'] != m_id]
                        else:
                            new_res = pd.DataFrame([{"id": m_id, "status": "雨天中止", "score": "-"}])
                            updated_res_df = pd.concat([res_df[res_df['id'] != m_id], new_res], ignore_index=True)
                        
                        conn.update(worksheet="results", data=updated_res_df)
                        st.cache_data.clear()
                        
                        st.success("スケジュールを削除し、対戦カードをプールに戻しました。該当のスコアレコードも削除されました。")
                        st.rerun()

                # 「不戦敗」への更新処理
                elif status == "不戦敗":
                    lose_team = st.radio(
                        "どちらのチームが不戦敗（負け）となりましたか？", 
                        [row['team1'], row['team2']], 
                        key=f"lose_{m_id}"
                    )
                    
                    if st.button("結果を保存", key=f"save_forfeit_{m_id}"):
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

# --- タブ4: 各種マスタデータの確認 ---
with tab4:
    st.header("⚙️ 登録データ・全シートの生確認")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🏟️ グラウンドマスタ")
        st.dataframe(grounds_df)
        st.subheader("📋 確保グラウンド枠の全履歴 (`available_slots`)")
        st.dataframe(slots_df)
    with c2:
        st.subheader("🏃 チームマスタ")
        st.dataframe(teams_df)
        st.subheader("🔥 残りの未消化試合プール")
        st.dataframe(pool_df)
from flask import Flask, request, jsonify
from flask_cors import CORS
import pymysql
import os
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)  # 允许前端跨域访问

def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "venue_db"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    action = data.get('action')

    if action == 'get_venues':
        conn = get_db_connection()
        cur = conn.cursor()

        # 查询会所基础信息
        cur.execute("""
            SELECT id, name, address, phone, business_hours,
                   map_url, cover_image, search_tags, technician_codes, status
            FROM venues WHERE status = 'active'
        """)
        venues = cur.fetchall()

        # 处理每个会所的附加数据
        for v in venues:
            # 解析搜索标签
            v['search_tags'] = v['search_tags'].split(',') if v['search_tags'] else []

            # 解析全体技师工号
            v['technician_list'] = []
            if v.get('technician_codes'):
                v['technician_list'] = [t.strip() for t in v['technician_codes'].split(',') if t.strip()]
            v['on_duty_count'] = len(v['technician_list'])

            # 查询该会所的服务项目
            cur.execute("""
                SELECT id, category, category_name, name, description,
                       price, duration, sort_order
                FROM service_items
                WHERE venue_id = %s
                ORDER BY category, sort_order
            """, (v['id'],))
            v['service_items'] = cur.fetchall()

            # 兼容前端原有字段
            v['technicians'] = []

        cur.close()
        conn.close()
        return jsonify({"venues": venues})

    return jsonify({"error": "Unknown action"}), 400

if __name__ == '__main__':
    print("🚀 后端 API 启动：http://127.0.0.1:8000")
    app.run(host='127.0.0.1', port=8000, debug=True)
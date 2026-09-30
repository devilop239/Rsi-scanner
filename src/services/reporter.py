from datetime import date
from typing import List
from src.services.scanner import SignalRecord

def generate_html_report(
    oversold: List[SignalRecord],
    overbought: List[SignalRecord],
    trading_date: date,
    low_thresh: float,
    high_thresh: float
) -> bytes:
    """Generate a premium, responsive HTML report for the market scan."""
    
    date_str = trading_date.strftime("%d %b %Y")
    parts = []
    
    # Helper for safe company name truncation
    def fmt_name(name: str) -> str:
        return name[:32] + "…" if len(name) > 32 else name

    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nifty 50 Scan — {date_str}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-body: #f1f5f9;
            --bg-card: #ffffff;
            --text-primary: #0f172a;
            --text-secondary: #64748b;
            --border: #e2e8f0;
            --green-bg: #dcfce7;
            --green-text: #166534;
            --red-bg: #fee2e2;
            --red-text: #991b1b;
            --accent: #3b82f6;
        }}
        
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        
        body {{
            background-color: var(--bg-body);
            color: var(--text-primary);
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.5;
            padding: 24px 16px;
            -webkit-font-smoothing: antialiased;
        }}
        
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background: var(--bg-card);
            border-radius: 16px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 10px 15px -3px rgba(0, 0, 0, 0.05);
            overflow: hidden;
            border: 1px solid var(--border);
        }}
        
        .header {{
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            padding: 32px 24px;
            text-align: center;
        }}
        
        .header h1 {{
            font-size: 24px;
            font-weight: 700;
            letter-spacing: -0.5px;
            margin-bottom: 8px;
        }}
        
        .header p {{
            font-size: 14px;
            color: #94a3b8;
            font-weight: 500;
        }}
        
        .content {{ padding: 32px 24px; }}
        
        .section-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .title-green {{ color: var(--green-text); }}
        .title-red {{ color: var(--red-text); }}
        
        /* Responsive Table Wrapper */
        .table-wrapper {{
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            border-radius: 12px;
            border: 1px solid var(--border);
            margin-bottom: 40px;
            background: #fafafa;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            min-width: 500px; /* Forces horizontal scroll on mobile instead of squishing */
            font-size: 14px;
        }}
        
        th {{
            background-color: #f8fafc;
            color: var(--text-secondary);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
            padding: 14px 16px;
            text-align: left;
            border-bottom: 1px solid var(--border);
            white-space: nowrap;
        }}
        
        td {{
            padding: 16px;
            border-bottom: 1px solid var(--border);
            color: var(--text-primary);
            vertical-align: middle;
        }}
        
        tr:last-child td {{ border-bottom: none; }}
        tr:hover td {{ background-color: #ffffff; }}
        
        .ticker {{
            font-weight: 700;
            color: var(--accent);
            font-family: 'SF Mono', 'Fira Code', monospace;
            font-size: 13px;
        }}
        
        .company {{
            color: var(--text-secondary);
            font-weight: 500;
            max-width: 180px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        
        .price {{
            font-weight: 600;
            font-variant-numeric: tabular-nums;
        }}
        
        .badge {{
            display: inline-flex;
            align-items: center;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 13px;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
        }}
        
        .badge-green {{ background: var(--green-bg); color: var(--green-text); }}
        .badge-red {{ background: var(--red-bg); color: var(--red-text); }}
        
        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: var(--text-secondary);
        }}
        
        .empty-state svg {{
            width: 48px;
            height: 48px;
            margin-bottom: 16px;
            opacity: 0.5;
        }}
        
        .footer {{
            text-align: center;
            padding: 24px;
            font-size: 12px;
            color: var(--text-secondary);
            background-color: #f8fafc;
            border-top: 1px solid var(--border);
            line-height: 1.6;
        }}
        
        /* Mobile optimizations */
        @media (max-width: 600px) {{
            body {{ padding: 12px 8px; }}
            .header {{ padding: 24px 16px; }}
            .header h1 {{ font-size: 20px; }}
            .content {{ padding: 20px 16px; }}
            .section-title {{ font-size: 16px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 Nifty 50 StochRSI Scan</h1>
            <p>Market Report for {date_str}</p>
        </div>
        <div class="content">
""")

    # --- OVERSOLD SECTION ---
    if oversold:
        parts.append(f"""
            <h2 class="section-title title-green">
                <span>🟢</span> Oversold Zone (StochRSI &lt; {low_thresh})
            </h2>
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Ticker</th>
                            <th>Company</th>
                            <th>Price</th>
                            <th>RSI</th>
                            <th>Stoch %K</th>
                        </tr>
                    </thead>
                    <tbody>
""")
        for s in oversold:
            parts.append(f"""
                        <tr>
                            <td class="ticker">{s.ticker.replace('.NS', '')}</td>
                            <td class="company" title="{s.company_name}">{fmt_name(s.company_name)}</td>
                            <td class="price">₹{s.close:,.2f}</td>
                            <td>{s.rsi:.1f}</td>
                            <td><span class="badge badge-green">{s.stoch_k:.1f}</span></td>
                        </tr>
""")
        parts.append("""
                    </tbody>
                </table>
            </div>
""")

    # --- OVERBOUGHT SECTION ---
    if overbought:
        parts.append(f"""
            <h2 class="section-title title-red">
                <span>🔴</span> Overbought Zone (StochRSI &gt; {high_thresh})
            </h2>
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Ticker</th>
                            <th>Company</th>
                            <th>Price</th>
                            <th>RSI</th>
                            <th>Stoch %K</th>
                        </tr>
                    </thead>
                    <tbody>
""")
        for s in overbought:
            parts.append(f"""
                        <tr>
                            <td class="ticker">{s.ticker.replace('.NS', '')}</td>
                            <td class="company" title="{s.company_name}">{fmt_name(s.company_name)}</td>
                            <td class="price">₹{s.close:,.2f}</td>
                            <td>{s.rsi:.1f}</td>
                            <td><span class="badge badge-red">{s.stoch_k:.1f}</span></td>
                        </tr>
""")
        parts.append("""
                    </tbody>
                </table>
            </div>
""")

    # --- EMPTY STATE ---
    if not oversold and not overbought:
        parts.append("""
            <div class="empty-state">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p style="font-size: 16px; font-weight: 500; color: #0f172a; margin-bottom: 8px;">All Clear Today</p>
                <p style="font-size: 14px;">No setups detected in the specified extreme zones. The market is consolidating.</p>
            </div>
""")

    # --- FOOTER ---
    parts.append("""
        </div>
        <div class="footer">
            ⚠️ <strong>Disclaimer:</strong> Setups are detected algorithmically for informational purposes only.<br>
            This is not investment advice. Always conduct your own research (DYOR) before trading.
        </div>
    </div>
</body>
</html>
""")

    return "".join(parts).encode("utf-8")
from flask import Flask, request, send_file, jsonify
import pandas as pd
from io import BytesIO
from datetime import timedelta, time, datetime
import tempfile
import os

app = Flask(__name__, static_folder='templates')

# Global variable to store the latest result (for demo purposes)
# In production, use proper caching or database storage
latest_result = None

def normalize_columns(df):
    """Normalize column names: strip spaces and convert to uppercase."""
    df.columns = [str(col).strip().upper().replace(' ', '_') for col in df.columns]
    return df

@app.route('/')
def home():
    return app.send_static_file('index.html')

@app.route('/process', methods=['POST'])
def process():
    try:
        if 'allocation_file' not in request.files or 'associate_file' not in request.files:
            return jsonify({'error': 'Both files are required'}), 400

        alloc_file = request.files['allocation_file']
        assoc_file = request.files['associate_file']

        if alloc_file.filename == '' or assoc_file.filename == '':
            return jsonify({'error': 'Please select both files'}), 400

        alloc_df = normalize_columns(pd.read_excel(alloc_file))
        assoc_df = normalize_columns(pd.read_excel(assoc_file))

        alloc_required = {'ESTIMATE', 'CONTRACTOR', 'CODE', 'ASSET_TAGGING'}
        assoc_required = {'ASSOCIATE_NAME', 'EIN', 'TEAM_LEADER'}

        if alloc_required - set(alloc_df.columns):
            return jsonify({'error': 'Allocation file missing required columns'}), 400
        if assoc_required - set(assoc_df.columns):
            return jsonify({'error': 'Associate file missing required columns'}), 400

        assoc_df = assoc_df.drop_duplicates('EIN').dropna(subset=['EIN'])
        associates = assoc_df['ASSOCIATE_NAME'].tolist()
        alloc_df['ASSIGNED_TO'] = [associates[i % len(associates)] for i in range(len(alloc_df))]

        result_df = pd.merge(alloc_df, assoc_df, left_on='ASSIGNED_TO', right_on='ASSOCIATE_NAME', how='left')
        result_df.drop(columns=['ASSIGNED_TO'], inplace=True)

        output = BytesIO()
        result_df.to_excel(output, index=False, engine='openpyxl')
        output.seek(0)

        return send_file(
            output,
            as_attachment=True,
            download_name='allocations_distributed.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        return jsonify({'error': f'Processing error: {str(e)}'}), 500

@app.route('/pending', methods=['POST'])
def pending():
    try:
        if 'main_file' not in request.files or 'daily_file' not in request.files:
            return jsonify({'error': 'Both main and daily files are required'}), 400

        main_df = normalize_columns(pd.read_excel(request.files['main_file']))
        daily_df = normalize_columns(pd.read_excel(request.files['daily_file']))

        if 'ESTIMATE' not in main_df.columns:
            return jsonify({'error': 'Main file must contain ESTIMATE column'}), 400

        est_col = None
        for col in daily_df.columns:
            if daily_df[col].astype(str).str.match(r'^[A-Z0-9]{8}$').any():
                est_col = col
                break

        if est_col is None:
            return jsonify({'error': 'No valid ESTIMATE column found in daily file'}), 400

        all_estimates = set(main_df['ESTIMATE'].dropna().astype(str))
        done_estimates = set(daily_df[est_col].dropna().astype(str))
        pending_estimates = all_estimates - done_estimates

        pending_df = main_df[main_df['ESTIMATE'].astype(str).isin(pending_estimates)]

        output = BytesIO()
        pending_df.to_excel(output, index=False, engine='openpyxl')
        output.seek(0)

        return send_file(
            output,
            as_attachment=True,
            download_name='pending_allocations.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        return jsonify({'error': f'Pending processing error: {str(e)}'}), 500

@app.route('/pending-multiple', methods=['POST'])
def pending_multiple():
    """Compare main and daily files pairwise to find pending allocations."""
    if 'main_files' not in request.files or 'daily_files' not in request.files:
        return "Both main and daily files are required", 400

    main_files = request.files.getlist('main_files')
    daily_files = request.files.getlist('daily_files')

    if len(main_files) != len(daily_files):
        return "The number of main files and daily files must be equal for accurate comparison.", 400

    all_pending = []

    for main_file, daily_file in zip(main_files, daily_files):
        main_df = normalize_columns(pd.read_excel(main_file))
        daily_df = normalize_columns(pd.read_excel(daily_file))

        if 'NAME' not in main_df.columns or 'ESTIMATE' not in main_df.columns:
            print(f"Skipping {main_file.filename}: Missing 'NAME' or 'ESTIMATE'")
            continue

        # Find valid ESTIMATE column in daily file
        est_col = None
        for col in daily_df.columns:
            if daily_df[col].astype(str).str.match(r'^[A-Z0-9]{8}$').any():
                est_col = col
                break

        if est_col is None:
            print(f"Skipping {daily_file.filename}: No valid ESTIMATE column")
            continue

        all_main_estimates = set(main_df['ESTIMATE'].dropna().astype(str))
        done_estimates = set(daily_df[est_col].dropna().astype(str))
        pending_estimates = all_main_estimates - done_estimates

        pending_df = main_df[main_df['ESTIMATE'].astype(str).isin(pending_estimates)].copy()

        # Add calculated PENDING_ALLOCATION column
        pending_allocations = []
        for _, row in pending_df.iterrows():
            estimate = str(row['ESTIMATE'])
            total_allocation = row.get('ALLOCATION', 0)

            try:
                total_allocation = float(total_allocation)
            except:
                total_allocation = 0

            daily_row = daily_df[daily_df[est_col].astype(str) == estimate]
            done_allocation = 0
            if not daily_row.empty and 'ESTIMATE NUMBER' in daily_row.columns:
                try:
                    done_allocation = float(daily_row.iloc[0]['ESTIMATE NUMBER'])
                except:
                    done_allocation = 0

            pending_allocation = total_allocation - done_allocation
            pending_allocations.append(pending_allocation)

        pending_df['PENDING_ALLOCATION'] = pending_allocations
        all_pending.append(pending_df)

    if not all_pending:
        return "No pending allocations found", 400

    result_df = pd.concat(all_pending, ignore_index=True)
    output = BytesIO()
    result_df.to_excel(output, index=False, engine='openpyxl')
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name='pending_allocations.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/download-pending-result')
def download_pending_result():
    global latest_result
    if not latest_result or not os.path.exists(latest_result):
        return jsonify({'error': 'No result available to download'}), 404
        
    return send_file(
        latest_result,
        as_attachment=True,
        download_name='pending_allocations.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/hourly', methods=['POST'])
def hourly_report():
    try:
        # Ensure the 'hourly_files' are in the request
        if 'hourly_files' not in request.files:
            return jsonify({'error': 'Hourly files are required'}), 400

        files = request.files.getlist('hourly_files')

        all_data = []

        def parse_duration(val):
            """Parse the duration to a timedelta object."""
            if pd.isnull(val):
                return timedelta(0)
            try:
                if isinstance(val, str):
                    t = datetime.strptime(val.strip(), "%H:%M:%S").time()
                    return timedelta(hours=t.hour, minutes=t.minute, seconds=t.second)
                if isinstance(val, time):
                    return timedelta(hours=val.hour, minutes=val.minute, seconds=val.second)
                return timedelta(seconds=float(val))
            except Exception:
                return timedelta(0)

        for file in files:
            if file.filename == '':
                continue  # Skip empty files

            # Read the Excel file, assuming the first row is the header
            df = pd.read_excel(file, header=0)

            # Rename columns to make them consistent
            df.columns = ['NAME', 'ID', 'ESTIMATE', 'DATE', 'FLAG', 'X', 'Y', 'DURATION'] + list(df.columns[8:])

            # Remove rows where the 'NAME' column contains the value 'NAME' (i.e., header data)
            df = df[df['NAME'] != 'NAME']

            # Apply the duration parsing to the 'DURATION' column
            df['DURATION'] = df['DURATION'].apply(parse_duration)

            # Append to the all_data list
            all_data.append(df)

        # If no valid data found, return error
        if not all_data:
            return jsonify({'error': 'No valid data found in uploaded files'}), 400

        # Combine all dataframes into one
        combined_df = pd.concat(all_data, ignore_index=True)

        # Group by 'NAME' and summarize the results
        summary = combined_df.groupby('NAME').agg(
            ESTIMATE_COUNT=('ESTIMATE', 'count'),
            TOTAL_DURATION=('DURATION', 'sum')
        ).reset_index()

        # Format the 'TOTAL_DURATION' into HH:MM:SS format
        summary['TOTAL_DURATION'] = summary['TOTAL_DURATION'].apply(
            lambda td: f"{int(td.total_seconds() // 3600):02}:{int((td.total_seconds() % 3600) // 60):02}:{int(td.total_seconds() % 60):02}"
        )

        # Save the result to an in-memory Excel file
        output = BytesIO()
        summary.to_excel(output, index=False, engine='openpyxl')
        output.seek(0)

        # Send the file as an attachment to the client
        return send_file(
            output,
            as_attachment=True,
            download_name='hourly_summary.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        # Return a generic error message
        return jsonify({'error': f'Hourly report error: {str(e)}'}), 500

    
if __name__ == '__main__':
    app.run(debug=True)
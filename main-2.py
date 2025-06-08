# Firebase Functions SDK 및 Admin SDK 임포트
from firebase_functions import firestore_fn, options, params
from firebase_admin import initialize_app, firestore
import smtplib # 이메일 발송을 위한 내장 라이브러리
from email.mime.text import MIMEText # 이메일 메시지 작성을 위함
from datetime import datetime # Firestore 타임스탬프 객체 처리를 위함
import pytz # 시간대 변환을 위함

# Firebase Admin SDK 초기화
initialize_app()

# 함수 실행 지역 설정 (실제 Firestore 리전과 맞추세요)
options.set_global_options(region=options.SupportedRegion.ASIA_NORTHEAST3) # 예시: 서울

# --- Firebase 환경 변수에서 설정값 가져오기 위한 매개변수 정의 ---
# 실제 값은 Firebase CLI를 통해 설정해야 합니다.
# (예: firebase functions:config:set gmail.email="your_email@gmail.com")

GMAIL_EMAIL_PARAM = params.SecretParam("GMAIL_EMAIL") # 또는 "gmail.email"
GMAIL_PASSWORD_PARAM = params.SecretParam("GMAIL_PASSWORD") # 또는 "gmail.password"
ALERT_RECIPIENT_EMAIL_PARAM = params.StringParam("ALERT_RECIPIENT_EMAIL", default="recipient@example.com") # 또는 "alert.recipient_email"

# 센서 값 임계치 (새로운 조건 반영)
# C 코드에서 & 0x3F 마스킹 (0-63 범위)된 ADC 값을 기준으로 합니다.
THRESHOLD_GAS_HIGH_PARAM = params.IntParam("THRESHOLD_GAS_HIGH", default=50) # 이 값 이상이면 위험
THRESHOLD_TEMP_LOW_ADC_PARAM = params.IntParam("THRESHOLD_TEMP_LOW_ADC", default=10) # 이 값 이하이면 위험 (온도 높음)
THRESHOLD_FLAME_LOW_ADC_PARAM = params.IntParam("THRESHOLD_FLAME_LOW_ADC", default=11) # 이 값 이하이면 위험 (불꽃 감지)


def send_email_alert(subject, html_body, recipient_email, gmail_user, gmail_password):
    """지정된 내용으로 이메일을 발송합니다."""
    if not gmail_user or not gmail_password or gmail_user == "YOUR_GMAIL_ADDRESS@gmail.com":
        print("Gmail 사용자 이름 또는 비밀번호가 Firebase 환경 변수에 올바르게 설정되지 않았습니다. 이메일 발송을 건너뜁니다.")
        return False
    try:
        msg = MIMEText(html_body, 'html', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = f"화재 감지 시스템 알리미 <{gmail_user}>"
        msg['To'] = recipient_email

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp_server:
            smtp_server.login(gmail_user, gmail_password)
            smtp_server.sendmail(gmail_user, recipient_email, msg.as_string())
        print(f"이메일이 성공적으로 발송되었습니다: {recipient_email}")
        return True
    except Exception as e:
        print(f"이메일 발송 중 오류 발생: {e}")
        return False

# Firestore의 'fire_detection_system_final' 컬렉션에 새 문서가 "생성될 때만" 트리거
@firestore_fn.on_document_created(document="fire_detection_system_final/{logId}")
def check_fire_alert_on_new_log(event: firestore_fn.Event[firestore_fn.Change]) -> None:
    """Firestore 새 문서 생성 시 센서 값을 확인하고 조건에 맞으면 이메일을 보냅니다."""
   
    if event.data is None: # 이론적으로 on_document_created에서는 항상 데이터가 있음
        print(f"이벤트 데이터가 없습니다 (ID: {event.params.get('logId', '알 수 없음')}). 처리를 건너뜁니다.")
        return

    new_data = event.data.to_dict() # 새로 생성된 문서의 데이터
    log_id = event.params.get('logId', '알 수 없는 ID')

    print(f"새 데이터 수신 (ID: {log_id}): {new_data}")
    
    event_timestamp_obj = new_data.get('event_timestamp')
    # 환경 변수에서 설정값 가져오기
    try:
       gmail_user = GMAIL_EMAIL_PARAM.value()
       gmail_password = GMAIL_PASSWORD_PARAM.value()
    except Exception as e:
        print(f"ERROR while loading local setup {e}")
        return
    
    
    recipient_email = ALERT_RECIPIENT_EMAIL_PARAM.value()
   
    threshold_gas_high = THRESHOLD_GAS_HIGH_PARAM.value()
    threshold_temp_low_adc = THRESHOLD_TEMP_LOW_ADC_PARAM.value()
    threshold_flame_low_adc = THRESHOLD_FLAME_LOW_ADC_PARAM.value()

    # Firestore 문서에서 센서 값 가져오기
    gas_adc_masked = new_data.get('gas_adc_masked')
    flame_adc_masked = new_data.get('flame_adc_masked')
    temp_adc_masked = new_data.get('temperature_adc_masked') # 온도 비교는 ADC 원시값으로
    # temperature_celsius = new_data.get('temperature_celsius_approx') # 이메일 내용에는 섭씨 온도 포함 가능

    event_timestamp_obj = new_data.get('event_timestamp')
    event_time_str = "시간 정보 없음"
    if isinstance(event_timestamp_obj, datetime):
        kst = pytz.timezone('Asia/Seoul')
        event_time_kst = event_timestamp_obj.astimezone(kst)
        event_time_str = event_time_kst.strftime('%Y년 %m월 %d일 %H시 %M분 %S초 %Z')
    elif isinstance(event_timestamp_obj, str):
        event_time_str = event_timestamp_obj

    alert_triggers = []
    is_alert_condition_met = False

    # 조건 확인 로직 (새로운 기준 적용)
    if gas_adc_masked is not None and gas_adc_masked >= threshold_gas_high:
        alert_triggers.append(f"가스 수치 높음: {gas_adc_masked} (기준: >= {threshold_gas_high})")
        is_alert_condition_met = True
   
    if temp_adc_masked is not None and temp_adc_masked <= threshold_temp_low_adc:
        # 온도는 ADC 값이 낮을수록 실제 온도가 높은 경우입니다.
        alert_triggers.append(f"온도 위험 (ADC 값 낮음): {temp_adc_masked} (기준: <= {threshold_temp_low_adc}) - 실제 온도 높을 가능성")
        is_alert_condition_met = True
   
    if flame_adc_masked is not None and flame_adc_masked <= threshold_flame_low_adc:
        # 불꽃 센서도 ADC 값이 낮을수록 불꽃 감지 가능성이 높은 경우입니다.
        alert_triggers.append(f"불꽃 감지 의심 (ADC 값 낮음): {flame_adc_masked} (기준: <= {threshold_flame_low_adc})")
        is_alert_condition_met = True

    # 조건 2: 1번 조건이 만족할 때만 메일을 보냄
    if is_alert_condition_met:
        alert_details_html = "".join([f"<li><strong>{trigger}</strong></li>" for trigger in alert_triggers])
       
        # 이메일에 포함할 추가 정보 (예: 변환된 섭씨 온도)
        temp_c_display = new_data.get('temperature_celsius_approx', 'N/A')
        if isinstance(temp_c_display, (int, float)):
            temp_c_display = f"{temp_c_display}°C"

        email_subject = f"[긴급] 화재 감지 시스템 경보!"
        email_html_body = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                    h1 {{ color: #FF0000; border-bottom: 2px solid #FF0000; padding-bottom: 10px;}}
                    strong {{ color: #B22222; }}
                    ul {{ list-style-type: square; padding-left: 20px; }}
                    pre {{ background-color: #f8f8f8; padding: 15px; border: 1px solid #eee; border-radius: 5px; font-size: 0.9em; white-space: pre-wrap; word-wrap: break-word;}}
                    .container {{ padding: 20px; border: 1px solid #ddd; border-radius: 8px; background-color: #fff9f9; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🚨 긴급 상황 발생: 화재 위험 감지 🚨</h1>
                    <p>Raspberry Pi 화재 감지 시스템에서 다음 경고 조건이 충족되었습니다:</p>
                    <ul>
                        {alert_details_html}
                    </ul>
                    <hr>
                    <h3>상세 정보:</h3>
                    <p><strong>감지 시간 (KST):</strong> {event_time_str}</p>
                    <p><strong>데이터 로그 ID:</strong> {log_id}</p>
                    <p><strong>수신된 전체 센서 값:</strong></p>
                    <pre>{new_data}</pre>
                    <p><strong>참고 - 변환된 섭씨 온도 (근사치):</strong> {temp_c_display}</p>
                    <p><strong>즉시 해당 위치의 안전을 확인하시고 필요한 조치를 취해주시기 바랍니다.</strong></p>
                    <p><i>(이 메일은 자동으로 발송되었습니다.)</i></p>
                </div>
            </body>
            </html>
        """
        send_email_alert(email_subject, email_html_body, recipient_email, gmail_user, gmail_password)
    else:
        print(f'센서 값 정상 (ID: {log_id}). 알림 조건 미충족.')

    

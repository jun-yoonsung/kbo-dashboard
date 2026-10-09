"""이닝 표기 변환."""


def format_innings(value):
    """계산된 소수 이닝(1/3이닝=0.3333...)을 KBO 표기('1.1'=1이닝+1아웃)로 변환한다.
    그냥 반올림하면(1.3333→'1.3') 야구에 없는 '.3이닝'처럼 보여 혼동을 준다."""
    if value is None or value != value:
        return ""
    whole = int(value)
    outs = int(round((value - whole) * 3))
    if outs >= 3:
        whole += 1
        outs -= 3
    return f"{whole}.{outs}"

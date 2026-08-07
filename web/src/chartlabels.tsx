// 차트 데이터라벨 공용 헬퍼.
// trendLabel: 추이(면적/라인) 차트는 점이 많으면 라벨이 겹치므로 최대 ~12개로 솎아서 표시.
// pieLabel: 도넛 슬라이스에 % 라벨(테마색), 4% 미만은 생략해 잡음 감소.

export function trendLabel(count: number, fmt: (v: number) => string, color: string) {
  const step = Math.max(1, Math.ceil(count / 12));
  return (props: any) => {
    const { x, y, value, index } = props;
    if (value == null || index % step !== 0) return null;
    return (
      <text x={x} y={y} dy={-6} textAnchor="middle" fontSize={9} fontWeight={600} fill={color}>
        {fmt(Number(value))}
      </text>
    );
  };
}

// 가로 막대 카테고리 축(매장/브랜드명 등) 단일 행 틱.
// recharts 기본 틱은 축 폭이 좁으면 긴 한글명을 2행으로 줄바꿈해 가독성이 나쁘다 → 항상 1행 렌더.
// 폭을 넘치는 긴 이름만 textLength로 살짝 가로압축(잘림 없음 → '무신사 스토어 성수'의 지점명 보존).
export function CatTick({ x, y, payload, fill, fontSize = 12, width = 130 }: any) {
  const text = String(payload?.value ?? "");
  // 자연 폭 추정: 한글/전각 ≈ fontSize, 그 외(라틴·숫자·공백) ≈ fontSize*0.56
  const natural = Array.from(text).reduce(
    (w: number, ch: string) =>
      w + (/[ᄀ-ᇿ　-鿿가-힣＀-￯]/.test(ch) ? fontSize : fontSize * 0.56),
    0,
  );
  const over = natural > width;
  return (
    <text x={x} y={y} dy={4} textAnchor="end" fontSize={fontSize} fill={fill}
      {...(over ? { textLength: width, lengthAdjust: "spacingAndGlyphs" } : {})}>
      <title>{text}</title>{text}
    </text>
  );
}

export function pieLabel(C: any) {
  const RAD = Math.PI / 180;
  return (p: any) => {
    const { cx, cy, midAngle, outerRadius, percent } = p;
    if (!percent || percent * 100 < 4) return null;
    const r = outerRadius + 16;
    const x = cx + r * Math.cos(-midAngle * RAD);
    const y = cy + r * Math.sin(-midAngle * RAD);
    return (
      <text x={x} y={y} fill={C.ttFg} fontSize={11} fontWeight={600} textAnchor={x > cx ? "start" : "end"} dominantBaseline="central">
        {(percent * 100).toFixed(0)}%
      </text>
    );
  };
}

export const ANSWER_TEXT = '客户端发送 SYN，服务器回复 SYN+ACK，客户端再回 ACK。'

function spanOf(snippet: string): { start: number; end: number } {
  const start = ANSWER_TEXT.indexOf(snippet)
  if (start < 0) {
    throw new Error(`fixture 里的证据不在原文中：${snippet}`)
  }
  return { start, end: start + snippet.length }
}

const P1_EVIDENCE = '客户端发送 SYN'
const P2_EVIDENCE = '服务器回复 SYN+ACK'
const D1_EVIDENCE = '再回 ACK'

export const HUMAN_ROW = {
  aggregate_id: 11,
  answer_id: 1,
  pseudo_id: 'S-0001',
  question_code: 'Q-01',
  raw_score: 5.5,
  final_score: null,
  max_score: 10,
  route: 'human',
  risk: 4.5,
  confidence: 0.6,
  dispersion: 0.4,
  total_range: 1,
  grounding_rate: 0.5,
  reasons: ['存在未接地判定（接地率 0.50）', '置信度 0.60 低于阈值 0.75'],
  created_at: '2026-10-28T00:00:00',
  reviewed: false,
}

export const AUTO_ROW = {
  ...HUMAN_ROW,
  aggregate_id: 12,
  answer_id: 2,
  pseudo_id: 'S-0002',
  raw_score: 8,
  risk: 0,
  confidence: 1,
  dispersion: 0,
  total_range: 0,
  grounding_rate: 1,
  reasons: [],
  route: 'auto',
}

export const DETAIL = {
  aggregate_id: 11,
  current: true,
  superseded_by: null,
  answer: {
    answer_id: 1,
    pseudo_id: 'S-0001',
    text: ANSWER_TEXT,
    truncated: false,
  },
  rubric: {
    question_id: 'Q-01',
    stem: '简述 TCP 三次握手',
    reference_answer: '',
    total_score: 10,
  },
  score: {
    raw: 5.5,
    final: null,
    max: 10,
    route: 'human',
    risk: 4.5,
    reasons: ['存在未接地判定（接地率 0.50）', '置信度 0.60 低于阈值 0.75'],
    dispersion: 0.4,
    total_range: 1,
    confidence: 0.6,
    grounding_rate: 0.5,
    sample_totals: [7, 4],
    applied_deductions: ['D1'],
  },
  items: [
    {
      id: 'P1',
      desc: '客户端发出 SYN',
      weight: 3,
      levels: [
        { level: 1, score: 3, criterion: '明确写出 SYN' },
        { level: 0, score: 0, criterion: '没有提到 SYN' },
      ],
      level: 0.5,
      score: 1.5,
      sample_levels: [1, 0],
      disagree: true,
      hit: true,
      grounded: true,
      evidence: P1_EVIDENCE,
      span: spanOf(P1_EVIDENCE),
      reason: '两份采样一个判命中、一个判未命中',
      samples: [
        {
          run_id: 1,
          hit: true,
          grounded: true,
          evidence: P1_EVIDENCE,
          span: spanOf(P1_EVIDENCE),
          level: 1,
        },
        {
          run_id: 2,
          hit: false,
          grounded: true,
          evidence: '',
          span: null,
          level: 0,
        },
      ],
    },
    {
      id: 'P2',
      desc: '服务器回复 SYN+ACK',
      weight: 4,
      levels: [
        { level: 1, score: 4, criterion: '明确写出 SYN+ACK' },
        { level: 0, score: 0, criterion: '没有回复' },
      ],
      level: 1,
      score: 4,
      sample_levels: [1, 1],
      disagree: false,
      hit: true,
      grounded: true,
      evidence: P2_EVIDENCE,
      span: spanOf(P2_EVIDENCE),
      reason: '',
      samples: [
        {
          run_id: 1,
          hit: true,
          grounded: true,
          evidence: P2_EVIDENCE,
          span: spanOf(P2_EVIDENCE),
          level: 1,
        },
        {
          run_id: 2,
          hit: true,
          grounded: true,
          evidence: P2_EVIDENCE,
          span: spanOf(P2_EVIDENCE),
          level: 1,
        },
      ],
    },
    {
      id: 'P3',
      desc: '客户端回 ACK',
      weight: 3,
      levels: [
        { level: 1, score: 3, criterion: '明确写出 ACK' },
        { level: 0, score: 0, criterion: '没有回 ACK' },
      ],
      level: 0,
      score: 0,
      sample_levels: [0, 0],
      disagree: false,
      hit: false,
      grounded: false,
      evidence: '',
      span: null,
      reason: '',
      samples: [],
    },
  ],
  deductions: [
    {
      id: 'D1',
      desc: '把 ACK 写成了第三次握手之后的额外消息',
      penalty: -1,
      cap: -2,
      applied: true,
      hit_samples: 1,
      evidence: D1_EVIDENCE,
      span: spanOf(D1_EVIDENCE),
      reason: '',
      samples: [],
    },
  ],
  samples: [
    { run_id: 1, score: 7, levels: { P1: 1, P2: 1, P3: 0 } },
    { run_id: 2, score: 4, levels: { P1: 0, P2: 1, P3: 0 } },
  ],
  review: null,
}

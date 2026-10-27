import type {
  GeneratedRubric,
  RubricData,
  RubricRecord,
  SavedRubric,
} from '../src/api/rubrics'

export const RUBRIC: RubricData = {
  question_id: 'Q-01',
  stem: '简述 TCP 三次握手的过程，并说明其目的。',
  reference_answer: 'SYN / SYN+ACK / ACK。',
  total_score: 10,
  items: [
    {
      id: 'P1',
      desc: '客户端发送 SYN 及初始序列号',
      weight: 3,
      levels: [
        { level: 1, score: 3, criterion: '明确 SYN=1、seq=x' },
        { level: 0.5, score: 1.5, criterion: '只提到发送连接请求' },
        { level: 0, score: 0, criterion: '未提及' },
      ],
    },
    {
      id: 'P2',
      desc: '服务器回复 SYN+ACK',
      weight: 4,
      levels: [
        { level: 1, score: 4, criterion: 'SYN、ACK、seq、ack 均正确' },
        { level: 0.5, score: 2, criterion: '只说服务器同意，缺字段' },
        { level: 0, score: 0, criterion: '未提及' },
      ],
    },
    {
      id: 'P3',
      desc: '客户端回复 ACK，连接建立',
      weight: 3,
      levels: [
        { level: 1, score: 3, criterion: 'ACK 与序号的确认关系正确' },
        { level: 0, score: 0, criterion: '未提及或错误' },
      ],
    },
  ],
  deductions: [{ id: 'D1', desc: '与四次挥手混淆', penalty: -2, cap: -2 }],
  rules: {},
}

export const GENERATED: GeneratedRubric = {
  rubric: RUBRIC,
  summary: '按三次握手的三个步骤拆成三个采分点。',
  preview: [],
  checklist: [],
  attempts: 1,
}

export const RECORD: RubricRecord = {
  rubric_id: 7,
  version: 2,
  rubric: RUBRIC,
  checksum: 'abc',
  source_text: '三个要点：客户端发 SYN 给 3 分；服务器回 SYN+ACK 给 4 分；客户端回 ACK 给 3 分。',
  feedback: '',
  created_at: '2026-10-27T00:00:00',
  parent_rubric_id: 1,
  question_code: 'Q-01',
  issues: [],
}

export const SAVED: SavedRubric = {
  rubric_id: 101,
  question_code: 'Q-01',
  version: 1,
  checksum: 'def',
  parent_rubric_id: null,
  reused: false,
  created_at: '2026-10-28T00:00:00',
  issues: [],
}

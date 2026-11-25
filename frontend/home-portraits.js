window.HomePortraits = (() => {
  const hangings = [
    [
      ['R05', '汤姆·克鲁斯', 'celebrity'], ['C02', '夜刀神狗', 'creator'],
      ['R26', 'Lisa', 'celebrity'], ['R11', '亨利·卡维尔', 'celebrity'],
      ['R27', 'Jennie', 'celebrity'], ['C05', 'Lyaoay', 'creator'], ['R33', '梅西', 'celebrity'],
    ],
    [
      ['R18', '章若楠', 'celebrity'], ['F14', '夏洛克·福尔摩斯', 'character', 'left'],
      ['R13', '道枝骏佑', 'celebrity'], ['R29', '王嘉尔', 'celebrity'],
      ['R20', '古力娜扎', 'celebrity'], ['R12', '兔娘', 'creator'], ['R07', '高圆圆', 'celebrity'],
    ],
    [
      ['R14', '安雅·泰勒-乔伊', 'celebrity'], ['F07', '温以凡', 'character', 'left'],
      ['R04', '赵今麦', 'celebrity'], ['R32', 'C罗', 'celebrity'],
      ['R22', '刘亦菲', 'celebrity'], ['C06', '王墨绫子', 'creator'], ['R08', '戚薇', 'celebrity'],
    ],
    [
      ['R34', '科比·布莱恩特', 'celebrity'], ['C01', '张菲儿', 'creator'],
      ['R24', 'ROSÉ', 'celebrity'], ['R03', '易烊千玺', 'celebrity'],
      ['R15', '王星越', 'celebrity'], ['F04', '黄亦玫', 'character', 'left'], ['R02', '张员瑛', 'celebrity'],
    ],
    [
      ['R25', '智秀', 'celebrity'], ['F10', '杰克·道森', 'character', 'left'],
      ['F02', '黑寡妇', 'character'], ['R01', '吴磊', 'celebrity'],
      ['R23', '迪丽热巴', 'celebrity'], ['W02', '冯万宁', 'creator'], ['R16', '程潇', 'celebrity'],
    ],
    [
      ['R19', '高叶', 'celebrity'], ['C04', '钟晨瑶', 'creator'],
      ['R10', '柳智敏', 'celebrity', 'left'], ['R30', '井柏然', 'celebrity'],
      ['R06', '陈数', 'celebrity'], ['F05', '范闲', 'character', 'left'], ['R28', '张予曦', 'celebrity'],
    ],
    [
      ['R31', '布拉德·皮特', 'celebrity'], ['F11', '聂九罗', 'character'],
      ['C03', '小鹿饱饱', 'creator'], ['R09', '汤姆·希德勒斯顿', 'celebrity', 'left'],
      ['R17', '林允', 'celebrity'], ['W01', '陈泽', 'creator'], ['R21', '鞠婧祎', 'celebrity'],
    ],
  ];
  const credits = {
    R05: '《碟中谍5：神秘国度》剧照 · 摄影 David James，Paramount Pictures',
    R11: '亨利·卡维尔 · AI 西装换装肖像',
    R24: 'ROSÉ · 原图 TV10 / CC BY 3.0 · AI 改编舞台造型',
    R33: '梅西 · 巴黎圣日耳曼签约照，来源 messi.com',
    C02: '夜刀神狗 · 视频 5.10 秒处的原帧',
  };
  function imageUrl(id) {
    return 'assets/home-portraits/' + id + '.webp?v=49-2';
  }
  return {hangings, credits, imageUrl};
})();

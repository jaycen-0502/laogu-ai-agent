const assert = require('assert');
const { buildLaunchRequestBody } = require('./runner.cjs');

const body = buildLaunchRequestBody(
  {
    code: 'BUYER_001',
    profileId: 'profile-1',
    targetCodes: ['BUYER_001'],
    codes: ['BUYER_001'],
  },
  {
    selector: {
      code: 'BUYER_001',
      profileId: 'profile-1',
      targetCodes: ['BUYER_001'],
      internalOnly: true,
    },
    startUrls: ['https://example.com'],
    skipDefaultStartUrls: true,
  },
);

assert.deepStrictEqual(body, {
  selector: {
    code: 'BUYER_001',
    profileId: 'profile-1',
  },
  startUrls: ['https://example.com'],
  skipDefaultStartUrls: true,
});
